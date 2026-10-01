{#
  For incremental models: a string literal like '2026-09-27' (or '2026-08' with fmt '%Y-%m')
  = max(column) in the existing table minus `lookback_days`, formatted for comparison against a
  bronze partition column. It's resolved at compile time, so Athena's partition projection can
  prune bronze. A scalar subquery in the WHERE clause would scan every partition instead.
  Returns '0000' on the first (non-incremental) run, which compares below every partition.
#}
{% macro incremental_since(column, lookback_days, fmt='%Y-%m-%d') %}
  {%- if execute and is_incremental() -%}
    {%- set sql -%}
      select date_format(date_add('day', -{{ lookback_days }}, max({{ column }})), '{{ fmt }}')
      from {{ this }}
    {%- endset -%}
    {%- set value = run_query(sql).columns[0].values()[0] -%}
    {{ return(value if value else '0000') }}
  {%- else -%}
    {{ return('0000') }}
  {%- endif -%}
{% endmacro %}

{# ISO-8601 'Z' string or Open-Meteo 'YYYY-MM-DDTHH:MM' (already UTC) -> timestamp(6) UTC. #}
{% macro utc_ts(expr) -%}
  cast(from_iso8601_timestamp({{ expr }}) as timestamp(6))
{%- endmacro %}

{% macro openmeteo_ts(expr) -%}
  cast(date_parse({{ expr }}, '%Y-%m-%dT%H:%i') as timestamp(6))
{%- endmacro %}

{#
  Partition filter for hourly bronze tables (dt=YYYY-MM-DD/hour=HH): only the hours since the
  latest *fetch* already in the table, minus `lookback_hours`. Filtering by day alone made every
  run list ~72 hourly folders per table (S3 requests are the main S3 cost). Keyed on fetch time,
  not on reading time, and after pipeline downtime the window reaches back to the last
  successful run. `max_lookback_hours` caps the window for tables whose fetch time can stall
  (OpenAQ PM2.5: during a source outage the hourly files have no rows, so no new fetch time is
  stored); downtime longer than the cap is recovered with scripts/backfill.py.
  Returns 'true' on the first (non-incremental) build.
#}
{% macro recent_hourly_partitions(fetch_column, lookback_hours=6, max_lookback_hours=none,
                                  dt='b.dt', hour='b.hour') %}
  {%- if execute and is_incremental() -%}
    {%- set sql -%}
      with c as (
        select
          {%- if max_lookback_hours %}
          greatest(max({{ fetch_column }}) - interval '{{ lookback_hours }}' hour,
                   cast(current_timestamp as timestamp(6))
                     - interval '{{ max_lookback_hours }}' hour) as cutoff
          {%- else %}
          max({{ fetch_column }}) - interval '{{ lookback_hours }}' hour as cutoff
          {%- endif %}
        from {{ this }}
      )
      select date_format(cutoff, '%Y-%m-%d'), date_format(cutoff, '%H') from c
    {%- endset -%}
    {%- set row = run_query(sql).rows[0] -%}
    {%- if row[0] -%}
      {{ return("(" ~ dt ~ " > '" ~ row[0] ~ "' or (" ~ dt ~ " = '" ~ row[0] ~ "' and "
                ~ hour ~ " >= '" ~ row[1] ~ "'))") }}
    {%- endif -%}
  {%- endif -%}
  {{ return('true') }}
{% endmacro %}

{# Literal (dt, hour) cutoff `hours` before now, for queries outside models (e.g. freshness). #}
{% macro hours_ago_partition_filter(hours, dt='dt', hour='hour') -%}
  {%- set t = modules.datetime.datetime.utcnow() - modules.datetime.timedelta(hours=hours) -%}
  ({{ dt }} > '{{ t.strftime("%Y-%m-%d") }}' or ({{ dt }} = '{{ t.strftime("%Y-%m-%d") }}'
   and {{ hour }} >= '{{ t.strftime("%H") }}'))
{%- endmacro %}

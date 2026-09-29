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

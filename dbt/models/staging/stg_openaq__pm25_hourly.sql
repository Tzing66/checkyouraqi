{#
  One row per station and OpenAQ hour: backfill history + live hourly files, deduplicated.
  OpenAQ hours are IST-aligned, so hour_start_utc is at :30 (e.g. 09:30-10:30 UTC).
  Overlaps (month edges, the 6h live lookback) are resolved by keeping the latest fetch.
#}
{{ config(
    materialized='incremental',
    incremental_strategy='merge',
    unique_key=['location_id', 'hour_start_utc'],
    partitioned_by=['month(hour_start_utc)'],
    on_schema_change='append_new_columns'
) }}

{%- set since_month = incremental_since('hour_start_utc', 40, '%Y-%m') -%}
{%- set since_day = incremental_since('hour_start_utc', 2) %}

with history as (
    select
        h.location_id,
        m.m as measurement,
        {{ utc_ts('h.fetched_at') }} as fetched_at,
        'history' as source
    from {{ source('bronze', 'openaq_pm25_history') }} as h
    cross join unnest(h.pages) as p (page)
    cross join unnest(page.results) as m (m)
    where h.month >= '{{ since_month }}'
),

live as (
    select
        cast(s.location_id as bigint) as location_id,
        m.m as measurement,
        {{ utc_ts('b.fetched_at') }} as fetched_at,
        'hourly' as source
    from {{ source('bronze', 'openaq_pm25_hours') }} as b
    cross join unnest(b.stations) as s (location_id, pages)
    cross join unnest(s.pages) as p (page)
    cross join unnest(page.results) as m (m)
    where b.dt >= '{{ since_day }}'
),

unioned as (
    select * from history
    union all
    select * from live
),

typed as (
    select
        location_id,
        {{ utc_ts('measurement.period.datetimefrom.utc') }} as hour_start_utc,
        {{ utc_ts('measurement.period.datetimeto.utc') }} as hour_end_utc,
        measurement.value as pm25,
        measurement.coverage.observedcount as observed_count,
        measurement.coverage.expectedcount as expected_count,
        measurement.coverage.percentcomplete as percent_complete,
        measurement.summary.min as pm25_min,
        measurement.summary.max as pm25_max,
        measurement.flaginfo.hasflags as has_openaq_flags,
        fetched_at,
        source,
        row_number() over (
            partition by location_id, measurement.period.datetimefrom.utc
            order by fetched_at desc, source
        ) as recency
    from unioned
    where measurement.parameter.name = 'pm25'
)

select
    location_id,
    hour_start_utc,
    hour_end_utc,
    pm25,
    observed_count,
    expected_count,
    percent_complete,
    pm25_min,
    pm25_max,
    has_openaq_flags,
    (pm25 is null
        or pm25 < {{ var('pm25_min') }}
        or pm25 > {{ var('pm25_max') }}) as is_out_of_range,
    fetched_at,
    source
from typed
where recency = 1

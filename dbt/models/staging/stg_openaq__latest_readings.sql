{#
  Latest reading per live sensor as seen by each hourly fetch. Merged on (sensor, reading
  time), so `last_fetched_at` keeps moving while a silent sensor's reading_utc stays put. That's
  exactly what the "last reading at ... (stale)" badges need. Dead legacy sensors are dropped.
#}
{{ config(
    materialized='incremental',
    incremental_strategy='merge',
    unique_key=['sensor_id', 'reading_utc'],
    partitioned_by=['month(reading_utc)'],
    on_schema_change='append_new_columns'
) }}

{%- set recent = recent_hourly_partitions('last_fetched_at') %}

with readings as (
    select
        cast(s.location_id as bigint) as location_id,
        r.m.sensorsid as sensor_id,
        {{ utc_ts('r.m.datetime.utc') }} as reading_utc,
        r.m.value as value,
        {{ utc_ts('b.fetched_at') }} as fetched_at
    from {{ source('bronze', 'openaq_latest') }} as b
    cross join unnest(b.stations) as s (location_id, pages)
    cross join unnest(s.pages) as p (page)
    cross join unnest(page.results) as r (m)
    where {{ recent }}
)

select
    r.location_id,
    r.sensor_id,
    ss.parameter,
    r.reading_utc,
    arbitrary(r.value) as value,
    min(r.fetched_at) as first_fetched_at,
    max(r.fetched_at) as last_fetched_at
from readings as r
inner join {{ ref('seed_station_sensors') }} as ss
    on r.sensor_id = ss.sensor_id
group by 1, 2, 3, 4

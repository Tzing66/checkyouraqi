{#
  CAMS PM2.5/PM10 history per zone point and hour (best estimate per hour, not an archived
  forecast). Leakage rule: only ever use it lagged, as known at prediction time minus 12h.
#}
{{ config(
    materialized='incremental',
    incremental_strategy='merge',
    unique_key=['point_id', 'time_utc'],
    partitioned_by=['month(time_utc)'],
    on_schema_change='append_new_columns'
) }}

{#- Incremental runs read only bronze files from the last 14 days (dt = first date a file
    covers): ~14 partition listings instead of ~640. After a manual backfill of older months,
    run with --full-refresh. #}
{%- set since_day = incremental_since('time_utc', 14) %}

with rows_ as (
    select
        pt.id as point_id,
        {{ openmeteo_ts('t.valid_time') }} as time_utc,
        t.pm2_5 as cams_pm25,
        t.pm10 as cams_pm10,
        {{ utc_ts('b.fetched_at') }} as fetched_at
    from {{ source('bronze', 'openmeteo_air_quality') }} as b
    cross join unnest(b.points, element_at(b.responses, 1)) as z (pt, r)
    cross join unnest(r.hourly.time, r.hourly.pm2_5, r.hourly.pm10)
        as t (valid_time, pm2_5, pm10)
    where b.dt >= '{{ since_day }}'
),

ranked as (
    select *, row_number() over (partition by point_id, time_utc order by fetched_at desc) as rn
    from rows_
    where cams_pm25 is not null
)

select point_id, time_utc, cams_pm25, cams_pm10, fetched_at
from ranked
where rn = 1

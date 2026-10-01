{# CAMS PM2.5/PM10 forecast per zone point, issue time and valid hour (the CAMS baseline). #}
{{ config(
    materialized='incremental',
    incremental_strategy='merge',
    unique_key=['point_id', 'issued_at', 'valid_time_utc'],
    partitioned_by=['month(issued_at)'],
    on_schema_change='append_new_columns'
) }}

{%- set recent = recent_hourly_partitions('issued_at') %}

select
    pt.id as point_id,
    {{ utc_ts('b.forecast_issued_at') }} as issued_at,
    {{ openmeteo_ts('t.valid_time') }} as valid_time_utc,
    date_diff('hour',
        date_trunc('hour', {{ utc_ts('b.forecast_issued_at') }}),
        {{ openmeteo_ts('t.valid_time') }}) as lead_hours,
    t.pm2_5 as cams_pm25,
    t.pm10 as cams_pm10
from {{ source('bronze', 'openmeteo_air_quality_forecasts') }} as b
cross join unnest(b.points, element_at(b.responses, 1)) as z (pt, r)
cross join unnest(r.hourly.time, r.hourly.pm2_5, r.hourly.pm10) as t (valid_time, pm2_5, pm10)
where {{ recent }}

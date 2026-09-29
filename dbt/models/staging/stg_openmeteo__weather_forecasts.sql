{#
  Weather forecast per zone point, per issue time and valid hour. `issued_at` is when we
  fetched it, so it certainly existed by then (leak-safe for features).
#}
{{ config(
    materialized='incremental',
    incremental_strategy='merge',
    unique_key=['point_id', 'issued_at', 'valid_time_utc'],
    partitioned_by=['month(issued_at)'],
    on_schema_change='append_new_columns'
) }}

{%- set since_day = incremental_since('issued_at', 2) %}

select
    pt.id as point_id,
    {{ utc_ts('b.forecast_issued_at') }} as issued_at,
    {{ openmeteo_ts('t.valid_time') }} as valid_time_utc,
    date_diff('hour',
        date_trunc('hour', {{ utc_ts('b.forecast_issued_at') }}),
        {{ openmeteo_ts('t.valid_time') }}) as lead_hours,
    t.temperature_2m,
    t.relative_humidity_2m,
    t.wind_speed_10m,
    t.wind_direction_10m,
    t.precipitation,
    t.surface_pressure,
    t.boundary_layer_height
from {{ source('bronze', 'openmeteo_forecasts') }} as b
cross join unnest(b.points, element_at(b.responses, 1)) as z (pt, r)
cross join unnest(
    r.hourly.time, r.hourly.temperature_2m, r.hourly.relative_humidity_2m,
    r.hourly.wind_speed_10m, r.hourly.wind_direction_10m, r.hourly.precipitation,
    r.hourly.surface_pressure, r.hourly.boundary_layer_height
) as t (
    valid_time, temperature_2m, relative_humidity_2m, wind_speed_10m, wind_direction_10m,
    precipitation, surface_pressure, boundary_layer_height
)
where b.dt >= '{{ since_day }}'

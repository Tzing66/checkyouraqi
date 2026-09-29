{#
  Historical forecasts: for each zone point and hour, the forecast as issued `days_before`
  (1/2/3/4) days earlier. These are the leak-free weather features for the 24/48/72h horizons.
  (No boundary layer height: Open-Meteo keeps no previous runs for it.)
#}
{{ config(materialized='table', partitioned_by=['month(time_utc)']) }}

with rows_ as (
    select
        pt.id as point_id,
        {{ openmeteo_ts('t.valid_time') }} as time_utc,
        1 as days_before,
        temperature_2m, relative_humidity_2m, wind_speed_10m, wind_direction_10m, precipitation, surface_pressure,
        {{ utc_ts('b.fetched_at') }} as fetched_at
    from {{ source('bronze', 'openmeteo_previous_runs') }} as b
    cross join unnest(b.points, element_at(b.responses, 1)) as z (pt, r)
    cross join unnest(r.hourly.time, r.hourly.temperature_2m_previous_day1, r.hourly.relative_humidity_2m_previous_day1, r.hourly.wind_speed_10m_previous_day1, r.hourly.wind_direction_10m_previous_day1, r.hourly.precipitation_previous_day1, r.hourly.surface_pressure_previous_day1) as t (valid_time, temperature_2m, relative_humidity_2m, wind_speed_10m, wind_direction_10m, precipitation, surface_pressure)
    union all
    select
        pt.id as point_id,
        {{ openmeteo_ts('t.valid_time') }} as time_utc,
        2 as days_before,
        temperature_2m, relative_humidity_2m, wind_speed_10m, wind_direction_10m, precipitation, surface_pressure,
        {{ utc_ts('b.fetched_at') }} as fetched_at
    from {{ source('bronze', 'openmeteo_previous_runs') }} as b
    cross join unnest(b.points, element_at(b.responses, 1)) as z (pt, r)
    cross join unnest(r.hourly.time, r.hourly.temperature_2m_previous_day2, r.hourly.relative_humidity_2m_previous_day2, r.hourly.wind_speed_10m_previous_day2, r.hourly.wind_direction_10m_previous_day2, r.hourly.precipitation_previous_day2, r.hourly.surface_pressure_previous_day2) as t (valid_time, temperature_2m, relative_humidity_2m, wind_speed_10m, wind_direction_10m, precipitation, surface_pressure)
    union all
    select
        pt.id as point_id,
        {{ openmeteo_ts('t.valid_time') }} as time_utc,
        3 as days_before,
        temperature_2m, relative_humidity_2m, wind_speed_10m, wind_direction_10m, precipitation, surface_pressure,
        {{ utc_ts('b.fetched_at') }} as fetched_at
    from {{ source('bronze', 'openmeteo_previous_runs') }} as b
    cross join unnest(b.points, element_at(b.responses, 1)) as z (pt, r)
    cross join unnest(r.hourly.time, r.hourly.temperature_2m_previous_day3, r.hourly.relative_humidity_2m_previous_day3, r.hourly.wind_speed_10m_previous_day3, r.hourly.wind_direction_10m_previous_day3, r.hourly.precipitation_previous_day3, r.hourly.surface_pressure_previous_day3) as t (valid_time, temperature_2m, relative_humidity_2m, wind_speed_10m, wind_direction_10m, precipitation, surface_pressure)
    union all
    select
        pt.id as point_id,
        {{ openmeteo_ts('t.valid_time') }} as time_utc,
        4 as days_before,
        temperature_2m, relative_humidity_2m, wind_speed_10m, wind_direction_10m, precipitation, surface_pressure,
        {{ utc_ts('b.fetched_at') }} as fetched_at
    from {{ source('bronze', 'openmeteo_previous_runs') }} as b
    cross join unnest(b.points, element_at(b.responses, 1)) as z (pt, r)
    cross join unnest(r.hourly.time, r.hourly.temperature_2m_previous_day4, r.hourly.relative_humidity_2m_previous_day4, r.hourly.wind_speed_10m_previous_day4, r.hourly.wind_direction_10m_previous_day4, r.hourly.precipitation_previous_day4, r.hourly.surface_pressure_previous_day4) as t (valid_time, temperature_2m, relative_humidity_2m, wind_speed_10m, wind_direction_10m, precipitation, surface_pressure)
),

ranked as (
    select *, row_number() over (
        partition by point_id, time_utc, days_before order by fetched_at desc
    ) as rn
    from rows_
    where temperature_2m is not null
)

select
    point_id, time_utc, days_before, temperature_2m, relative_humidity_2m, wind_speed_10m, wind_direction_10m, precipitation, surface_pressure, fetched_at
from ranked
where rn = 1

{#
  Fire detections placed relative to the city: great-circle distance and bearing from the city
  centre, 8-way compass sector, distance band. One satellite per day (SNPP, else NOAA-20) so
  counts aren't doubled; low-confidence detections dropped. Phase 3 combines sector with wind
  direction to get "upwind" fires. Only use fires acquired before prediction time (+ ~3h NRT
  publication lag).
#}
{{ config(materialized='table', partitioned_by=['month(acquired_at_utc)']) }}

{%- set lat0 = var('city_center_lat') -%}
{%- set lon0 = var('city_center_lon') %}

with per_sensor_day as (
    select acq_date, sensor, count(*) as n
    from {{ ref('stg_firms__fire_points') }}
    group by 1, 2
),

chosen as (
    -- Prefer SNPP; fall back to NOAA-20 on days SNPP has nothing.
    select acq_date, sensor
    from (
        select *, row_number() over (
            partition by acq_date order by case sensor when 'VIIRS_SNPP' then 0 else 1 end
        ) as rn
        from per_sensor_day
    )
    where rn = 1
),

points as (
    select
        f.*,
        radians(f.latitude) as phi2, radians({{ lat0 }}) as phi1,
        radians(f.longitude - {{ lon0 }}) as dlambda
    from {{ ref('stg_firms__fire_points') }} as f
    inner join chosen as c on f.acq_date = c.acq_date and f.sensor = c.sensor
    where f.confidence <> 'l'
),

geo as (
    select
        *,
        6371 * 2 * asin(sqrt(
            power(sin((phi2 - phi1) / 2), 2)
            + cos(phi1) * cos(phi2) * power(sin(dlambda / 2), 2)
        )) as distance_km,
        mod(degrees(atan2(
            sin(dlambda) * cos(phi2),
            cos(phi1) * sin(phi2) - sin(phi1) * cos(phi2) * cos(dlambda)
        )) + 360, 360) as bearing_deg
    from points
)

select
    'delhi_ncr' as city_id,
    sensor,
    product,
    cast(acquired_at_utc as timestamp(6)) as acquired_at_utc,
    acq_date,
    latitude,
    longitude,
    frp_mw,
    confidence,
    daynight,
    round(distance_km, 1) as distance_km,
    round(bearing_deg, 1) as bearing_deg,
    element_at(array['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'],
        cast(floor(mod(bearing_deg + 22.5, 360) / 45) as integer) + 1) as sector,
    case
        when distance_km < 100 then '0-100km'
        when distance_km < 250 then '100-250km'
        else '250km+'
    end as distance_band
from geo

{{ config(materialized='table') }}
select
    z.zone_id, z.zone_name, z.city_id,
    count(s.location_id) as station_count,
    wp.latitude as weather_point_lat,
    wp.longitude as weather_point_lon
from {{ ref('seed_zones') }} as z
left join {{ ref('seed_stations') }} as s on s.zone_id = z.zone_id
left join {{ ref('seed_weather_points') }} as wp on wp.point_id = z.zone_id
group by 1, 2, 3, 5, 6

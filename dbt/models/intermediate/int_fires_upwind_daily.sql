{# Daily fire counts and radiative power per city, sector and distance band (dashboard + EDA). #}
{{ config(materialized='table') }}

select
    city_id,
    acq_date,
    sector,
    distance_band,
    count(*) as fire_count,
    round(sum(frp_mw), 1) as frp_sum_mw
from {{ ref('int_fire_points_located') }}
group by 1, 2, 3, 4

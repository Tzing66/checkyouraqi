{#
  City PM2.5 per hour = MEDIAN across its stations (so one faulty station can't distort it),
  excluding co-located duplicates and invalid hours, with the number of stations reporting.
#}
{{ config(materialized='table', partitioned_by=['month(hour_start_utc)']) }}

select
    city_id,
    hour_start_utc,
    approx_percentile(pm25, 0.5) as pm25_median,
    approx_percentile(pm25_24h, 0.5) as pm25_24h_median,
    count(pm25) as stations_reporting,
    count(pm25_24h) as stations_with_24h_avg,
    count(*) as stations_expected
from {{ ref('fct_aqi_hourly') }}
where not is_colocated_duplicate
group by 1, 2

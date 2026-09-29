{#
  One row per city and IST day (India's civil day, as CPCB reports): median of stations' daily
  mean PM2.5, the AQI category of that value, and the day's fire counts. Stored with the IST date
  explicitly named, since everything else is UTC.
#}
{{ config(materialized='table') }}

with station_day as (
    select
        city_id,
        location_id,
        cast(hour_start_utc + interval '5' hour + interval '30' minute as date) as date_ist,
        avg(pm25) as pm25_day_mean,
        count(pm25) as valid_hours
    from {{ ref('fct_aqi_hourly') }}
    where not is_colocated_duplicate
    group by 1, 2, 3
),

city_day as (
    select
        city_id,
        date_ist,
        round(approx_percentile(case when valid_hours >= {{ var('min_hours_for_24h_avg') }}
            then pm25_day_mean end, 0.5), 1) as pm25_median,
        count_if(valid_hours >= {{ var('min_hours_for_24h_avg') }}) as stations_with_full_day
    from station_day
    group by 1, 2
),

fires as (
    select city_id, acq_date, sum(fire_count) as fires_total,
           sum(case when sector in ('NW', 'W', 'N') then fire_count else 0 end) as fires_nw_arc
    from {{ ref('int_fires_upwind_daily') }}
    group by 1, 2
)

select
    c.city_id,
    c.date_ist,
    c.pm25_median,
    c.stations_with_full_day,
    b.category as aqi_category,
    coalesce(f.fires_total, 0) as fires_total,
    coalesce(f.fires_nw_arc, 0) as fires_nw_arc,
    d.is_festival,
    d.festival_name
from city_day as c
left join {{ ref('seed_aqi_breakpoints') }} as b
    on round(c.pm25_median) between b.pm25_low and coalesce(b.pm25_high, 1e9)
left join fires as f on f.city_id = c.city_id and f.acq_date = c.date_ist
left join {{ ref('dim_date') }} as d on d.date_ist = c.date_ist

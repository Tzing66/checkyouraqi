{#
  PM2.5 per station and hour with the CPCB 24h rolling average and India AQI category.
  - pm25_24h: mean of valid hours in the trailing 24h window, only if >= 16 valid hours (CPCB).
  - Category from the ROUNDED 24h average (CPCB breakpoints are whole numbers: 0-30, 31-60...).
  - Sub-index by linear interpolation; NULL in the open-ended "severe" band until the upper
    breakpoint is verified against CPCB (config/aqi_breakpoints.yaml TODO).
#}
{{ config(materialized='table', partitioned_by=['month(hour_start_utc)']) }}

with rolled as (
    select
        h.*,
        count(pm25) over w as valid_hours_24h,
        avg(pm25) over w as pm25_24h_raw
    from {{ ref('int_station_hourly') }} as h
    window w as (partition by location_id order by hour_start_utc
                 rows between 23 preceding and current row)
),

averaged as (
    select
        *,
        case when valid_hours_24h >= {{ var('min_hours_for_24h_avg') }}
            then round(pm25_24h_raw, 1) end as pm25_24h
    from rolled
)

select
    a.location_id,
    s.zone_id,
    s.city_id,
    a.hour_start_utc,
    a.hour_end_utc,
    a.pm25,
    a.pm25_raw,
    a.is_valid,
    a.is_missing,
    a.valid_hours_24h,
    a.pm25_24h,
    b.category as aqi_category,
    b.category_order as aqi_category_order,
    case when b.pm25_high is not null then cast(round(
        b.aqi_low + (b.aqi_high - b.aqi_low) * (round(a.pm25_24h) - b.pm25_low)
            / (b.pm25_high - b.pm25_low)
    ) as integer) end as pm25_subindex,
    s.colocated_with is not null as is_colocated_duplicate
from averaged as a
inner join {{ ref('seed_stations') }} as s on s.location_id = a.location_id
left join {{ ref('seed_aqi_breakpoints') }} as b
    on round(a.pm25_24h) between b.pm25_low and coalesce(b.pm25_high, 1e9)

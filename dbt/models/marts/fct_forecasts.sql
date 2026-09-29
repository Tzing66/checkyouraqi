{#
  Every forecast made, with its (indicative, hourly) AQI category and staleness label.
  Forecasts from stale inputs are kept (owner decision) but flagged so surfaces can warn.
#}
{{ config(materialized='table', partitioned_by=['month(issue_time_utc)']) }}

select
    p.location_id,
    s.zone_id,
    s.city_id,
    p.horizon_h,
    p.issue_time_utc,
    p.target_hour_start_utc,
    p.pm25_pred,
    b.category as predicted_aqi_category,
    p.input_age_hours,
    p.is_stale_input,
    p.last_valid_hour_utc,
    p.model_version,
    p.predicted_at
from {{ ref('stg_predictions') }} as p
inner join {{ ref('seed_stations') }} as s on s.location_id = p.location_id
left join {{ ref('seed_aqi_breakpoints') }} as b
    on round(p.pm25_pred) between b.pm25_low and coalesce(b.pm25_high, 1e9)

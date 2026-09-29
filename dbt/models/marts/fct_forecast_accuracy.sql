{#
  Forecasts whose target hour has passed, joined to what actually happened (valid hours only).
  Empty while the source is down; fills automatically once live PM2.5 flows again. The monitor
  DAG and dashboard aggregate this; until it has data they show the Phase 3 backtest.
#}
{{ config(materialized='table') }}

select
    f.location_id,
    f.zone_id,
    f.horizon_h,
    f.issue_time_utc,
    f.target_hour_start_utc,
    f.model_version,
    f.is_stale_input,
    f.pm25_pred,
    a.pm25 as pm25_actual,
    f.pm25_pred - a.pm25 as error,
    abs(f.pm25_pred - a.pm25) as abs_error,
    f.predicted_aqi_category,
    ab.category as actual_aqi_category,
    f.predicted_aqi_category = ab.category as category_correct
from {{ ref('fct_forecasts') }} as f
inner join {{ ref('fct_aqi_hourly') }} as a
    on a.location_id = f.location_id
    and a.hour_start_utc = f.target_hour_start_utc
    and a.is_valid
left join {{ ref('seed_aqi_breakpoints') }} as ab
    on round(a.pm25) between ab.pm25_low and coalesce(ab.pm25_high, 1e9)

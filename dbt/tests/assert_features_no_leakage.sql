{#
  Leakage guard (plan §7): every feature group's latest input must be at or before the
  prediction time, and the target strictly after it. Returns offending rows (must be none).
#}
select location_id, issue_time_utc, horizon_h
from {{ ref('fct_features_hourly') }}
where audit_station_obs_until > issue_time_utc
    or audit_obs_weather_time > issue_time_utc
    or audit_weather_forecast_available_by > issue_time_utc
    or audit_cams_time > issue_time_utc
    or audit_fires_published_by > issue_time_utc
    or target_hour_start_utc < issue_time_utc
    or date_diff('hour', issue_time_utc, target_hour_start_utc) <> horizon_h - 1

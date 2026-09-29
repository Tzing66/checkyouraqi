{# Same leakage guard for the live table (no target, so only the input-time audits). #}
select location_id, horizon_h
from {{ ref('fct_features_live') }}
where audit_station_obs_until > issue_time_utc
    or audit_obs_weather_time > issue_time_utc
    or audit_weather_forecast_available_by > issue_time_utc
    or audit_cams_time > issue_time_utc
    or audit_fires_published_by > issue_time_utc
    or date_diff('hour', issue_time_utc, target_hour_start_utc) <> horizon_h - 1

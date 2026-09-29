{# Stations keyed on OpenAQ's stable location id (never on names), with history quality stats. #}
{{ config(materialized='table') }}

with stats as (
    select
        location_id,
        min(hour_start_utc) as first_hour_utc,
        max(case when not is_missing then hour_start_utc end) as last_reported_hour_utc,
        count(*) as grid_hours,
        round(avg(cast(is_valid as double)) * 100, 1) as pct_valid_hours
    from {{ ref('int_station_hourly') }}
    group by 1
)

select
    s.location_id,
    s.station_name,
    s.agency,
    s.provider,
    s.latitude,
    s.longitude,
    s.pm25_sensor_id,
    s.zone_id,
    s.city_id,
    s.colocated_with,
    s.colocated_with is not null as is_colocated_duplicate,
    st.first_hour_utc,
    st.last_reported_hour_utc,
    coalesce(st.grid_hours, 0) as grid_hours,
    st.pct_valid_hours,
    st.first_hour_utc is null as has_no_history
from {{ ref('seed_stations') }} as s
left join stats as st on st.location_id = s.location_id

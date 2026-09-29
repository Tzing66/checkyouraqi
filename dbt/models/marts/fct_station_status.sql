{#
  Current state of every station for the "last reading ... / sensor inactive / service down"
  badges. Status is computed as of `snapshot_at` (build time). The dashboard/API recompute age
  at view time with ingestion/freshness.py, using the same thresholds (config/freshness.yaml).
#}
{{ config(materialized='table') }}

with latest as (
    select
        location_id,
        max(case when parameter = 'pm25' then reading_utc end) as last_pm25_reading_utc,
        max_by(case when parameter = 'pm25' then value end,
               case when parameter = 'pm25' then reading_utc end) as last_pm25_value,
        max(reading_utc) as last_any_reading_utc,
        max(last_fetched_at) as last_checked_utc
    from {{ ref('stg_openaq__latest_readings') }}
    group by 1
),

aged as (
    select
        s.location_id,
        s.station_name,
        s.zone_id,
        s.city_id,
        l.last_pm25_reading_utc,
        l.last_pm25_value,
        l.last_any_reading_utc,
        l.last_checked_utc,
        cast(current_timestamp as timestamp(6)) as snapshot_at,
        date_diff('minute', l.last_pm25_reading_utc, cast(current_timestamp as timestamp(6)))
            / 60.0 as age_hours
    from {{ ref('seed_stations') }} as s
    left join latest as l on l.location_id = s.location_id
)

select
    *,
    case
        when last_pm25_reading_utc is null then 'no_data'
        when age_hours <= {{ var('freshness_live_max_hours') }} then 'live'
        when age_hours <= {{ var('freshness_delayed_max_hours') }} then 'delayed'
        when age_hours <= {{ var('freshness_inactive_max_days') }} * 24 then 'inactive'
        else 'offline'
    end as status
from aged

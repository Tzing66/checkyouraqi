{#
  Per hour: what share of stations had a live reading (reported within the last
  freshness_live_max_hours), and the resulting feed status. >= 80% not live = the SOURCE is
  down (e.g. OpenAQ's CPCB stop from 2026-09-24), not the sensors. The latest row is "now".
#}
{{ config(materialized='table', partitioned_by=['month(hour_start_utc)']) }}

with last_seen as (
    select
        location_id,
        hour_start_utc,
        max(case when not is_missing then hour_start_utc end) over (
            partition by location_id order by hour_start_utc
            rows between unbounded preceding and current row
        ) as last_report_utc
    from {{ ref('int_station_hourly') }}
),

per_station as (
    select
        hour_start_utc,
        last_report_utc,
        coalesce(date_diff('hour', last_report_utc, hour_start_utc)
            <= {{ var('freshness_live_max_hours') }}, false) as is_live
    from last_seen
),

per_hour as (
    select
        hour_start_utc,
        count(*) as stations_tracked,
        count_if(is_live) as stations_live,
        round(1 - avg(cast(is_live as double)), 3) as share_not_live,
        max(last_report_utc) as newest_report_utc
    from per_station
    group by 1
)

select
    *,
    case
        when share_not_live >= {{ var('feed_outage_min_share_not_live') }} then 'outage'
        when share_not_live >= {{ var('feed_degraded_min_share_not_live') }} then 'degraded'
        else 'ok'
    end as feed_status
from per_hour

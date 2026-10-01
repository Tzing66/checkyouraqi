{#
  Rolling fire counts per compass sector for every hour, over windows ENDING
  firms_publish_lag_hours before that hour (NRT detections take ~3h to publish). Pre-aggregated
  so the feature table joins on equality instead of an expensive time-range join.
#}
{{ config(materialized='table', partitioned_by=['month(as_of_utc)']) }}

{%- set lag = var('firms_publish_lag_hours') %}

with hourly as (
    select
        sector,
        date_trunc('hour', acquired_at_utc) as hour_utc,
        count(*) as n,
        sum(frp_mw) as frp
    from {{ ref('int_fire_points_located') }}
    group by 1, 2
),

bounds as (
    select min(hour_utc) as first_hour from hourly
),

grid as (
    select s.sector, g.hour_utc
    from bounds as b
    cross join unnest(array['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']) as s (sector)
    cross join unnest(sequence(
        -- Built daily, read hourly: extend ~26h past the build so live features always find
        -- a row. Future rows only hold fires already known at build time (no leakage).
        b.first_hour,
        date_trunc('hour', cast(current_timestamp as timestamp(6))) + interval '26' hour,
        interval '1' hour)) as g (hour_utc)
),

filled as (
    select g.sector, g.hour_utc, coalesce(h.n, 0) as n, coalesce(h.frp, 0) as frp
    from grid as g
    left join hourly as h on h.sector = g.sector and h.hour_utc = g.hour_utc
),

rolled as (
    select
        sector,
        hour_utc,
        sum(n) over (partition by sector order by hour_utc
                     rows between 23 preceding and current row) as n_24h,
        sum(n) over (partition by sector order by hour_utc
                     rows between 47 preceding and current row) as n_48h,
        sum(n) over (partition by sector order by hour_utc
                     rows between 71 preceding and current row) as n_72h,
        sum(frp) over (partition by sector order by hour_utc
                       rows between 23 preceding and current row) as frp_24h
    from filled
)

select
    sector,
    cast(array_position(array['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'], sector) - 1
        as integer) as sector_index,
    -- Counts in these rows cover fires acquired up to hour_utc (+1h); they are usable by a
    -- prediction made at as_of_utc = hour_utc + 1h + publish lag.
    date_add('hour', 1 + {{ lag }}, hour_utc) as as_of_utc,
    date_add('hour', 1, hour_utc) as window_end_utc,
    n_24h,
    n_48h,
    n_72h,
    round(frp_24h, 1) as frp_24h
from rolled

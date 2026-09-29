{#
  Complete hourly grid per station, from its first reading up to the current hour. Gaps are
  FLAGGED, not filled (plan §6). `is_valid` is the row-level quality verdict used for training
  and for aggregates. Hours are OpenAQ's IST-aligned hours (start at :30 UTC).
#}
{{ config(materialized='table', partitioned_by=['month(hour_start_utc)']) }}

with bounds as (
    select
        location_id,
        min(hour_start_utc) as first_hour
    from {{ ref('stg_openaq__pm25_hourly') }}
    group by location_id
),

-- Last complete IST-aligned hour: now, floored to :30.
now_hour as (
    select date_add('minute', 30,
        date_trunc('hour', date_add('minute', -30, cast(current_timestamp as timestamp(6))))
    ) - interval '1' hour as last_hour
),

grid as (
    select b.location_id, g.hour_start_utc
    from bounds as b
    cross join now_hour as n
    cross join unnest(sequence(b.first_hour, n.last_hour, interval '1' hour)) as g (hour_start_utc)
),

joined as (
    select
        g.location_id,
        g.hour_start_utc,
        m.pm25 as pm25_raw,
        m.percent_complete,
        m.observed_count,
        m.is_out_of_range,
        m.source,
        m.pm25 is null as is_missing
    from grid as g
    left join {{ ref('stg_openaq__pm25_hourly') }} as m
        on g.location_id = m.location_id and g.hour_start_utc = m.hour_start_utc
),

flagged as (
    select
        *,
        coalesce(percent_complete < {{ var('min_hour_coverage_pct') }}, false) as is_low_coverage,
        -- Stuck sensor: a full window of identical non-null values.
        (count(pm25_raw) over w = {{ var('flatline_hours') }}
            and max(pm25_raw) over w = min(pm25_raw) over w) as is_flatline
    from joined
    window w as (
        partition by location_id order by hour_start_utc
        rows between {{ var('flatline_hours') - 1 }} preceding and current row
    )
)

select
    location_id,
    hour_start_utc,
    date_add('hour', 1, hour_start_utc) as hour_end_utc,
    pm25_raw,
    case
        when not is_missing and not coalesce(is_out_of_range, false)
            and not is_low_coverage and not is_flatline
        then pm25_raw
    end as pm25,
    percent_complete,
    observed_count,
    is_missing,
    coalesce(is_out_of_range, false) as is_out_of_range,
    is_low_coverage,
    is_flatline,
    (not is_missing and not coalesce(is_out_of_range, false)
        and not is_low_coverage and not is_flatline) as is_valid,
    source
from flagged

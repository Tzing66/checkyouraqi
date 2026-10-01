{#
  Observed (ERA5) weather per zone point and hour. Files overlap (the daily job re-fetches a
  week), so keep the latest fetch per hour and drop hours ERA5 hasn't published yet (all null).
  Incremental: each run reads only recent bronze files (see below).
#}
{{ config(
    materialized='incremental',
    incremental_strategy='merge',
    unique_key=['point_id', 'time_utc'],
    partitioned_by=['month(time_utc)'],
    on_schema_change='append_new_columns'
) }}

{#- Incremental runs read only bronze files from the last 14 days (dt = first date a file
    covers): ~14 partition listings instead of ~640. After a manual backfill of older months,
    run with --full-refresh. #}
{%- set since_day = incremental_since('time_utc', 14) %}

with rows_ as (
    select
        pt.id as point_id,
        {{ openmeteo_ts('t.valid_time') }} as time_utc,
        t.temperature_2m, t.relative_humidity_2m, t.wind_speed_10m, t.wind_direction_10m,
        t.precipitation, t.surface_pressure, t.boundary_layer_height,
        {{ utc_ts('b.fetched_at') }} as fetched_at
    from {{ source('bronze', 'openmeteo_actuals') }} as b
    cross join unnest(b.points, element_at(b.responses, 1)) as z (pt, r)
    cross join unnest(
        r.hourly.time, r.hourly.temperature_2m, r.hourly.relative_humidity_2m,
        r.hourly.wind_speed_10m, r.hourly.wind_direction_10m, r.hourly.precipitation,
        r.hourly.surface_pressure, r.hourly.boundary_layer_height
    ) as t (
        valid_time, temperature_2m, relative_humidity_2m, wind_speed_10m, wind_direction_10m,
        precipitation, surface_pressure, boundary_layer_height
    )
    where b.dt >= '{{ since_day }}'
),

ranked as (
    select *, row_number() over (partition by point_id, time_utc order by fetched_at desc) as rn
    from rows_
    where temperature_2m is not null
)

select
    point_id, time_utc, temperature_2m, relative_humidity_2m, wind_speed_10m,
    wind_direction_10m, precipitation, surface_pressure, boundary_layer_height, fetched_at
from ranked
where rn = 1

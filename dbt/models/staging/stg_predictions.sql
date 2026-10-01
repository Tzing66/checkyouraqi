{# Live forecasts, one row per station, issue time and horizon (latest prediction run wins). #}
{{ config(
    materialized='incremental',
    incremental_strategy='merge',
    unique_key=['location_id', 'issue_time_utc', 'horizon_h'],
    partitioned_by=['month(issue_time_utc)'],
    on_schema_change='append_new_columns'
) }}

{%- set recent = recent_hourly_partitions('predicted_at') %}

with rows_ as (
    select
        p.r.location_id,
        p.r.zone_id,
        p.r.horizon_h,
        {{ utc_ts('p.r.issue_time_utc') }} as issue_time_utc,
        {{ utc_ts('p.r.target_hour_start_utc') }} as target_hour_start_utc,
        p.r.pm25_pred,
        p.r.input_age_hours,
        p.r.is_stale_input,
        {{ utc_ts('p.r.last_valid_hour_utc') }} as last_valid_hour_utc,
        p.r.model_version,
        {{ utc_ts('b.predicted_at') }} as predicted_at
    from {{ source('bronze', 'predictions') }} as b
    cross join unnest(b.predictions) as p (r)
    where {{ recent }}
),

ranked as (
    select *, row_number() over (
        partition by location_id, issue_time_utc, horizon_h order by predicted_at desc
    ) as rn
    from rows_
)

select
    location_id, zone_id, horizon_h, issue_time_utc, target_hour_start_utc, pm25_pred,
    input_age_hours, is_stale_input, last_valid_hour_utc, model_version, predicted_at
from ranked
where rn = 1

{#
  VIIRS fire detections, typed. Incremental: each run reads only the last 10 days of bronze
  files. Both products are kept when a day has archive (SP) and near-real-time (NRT) files;
  int_fire_points_located applies the SP-over-NRT preference, so a later SP file for an old
  day corrects it without deleting anything here. After a manual FIRMS backfill of older days,
  run with --full-refresh.
#}
{{ config(
    materialized='incremental',
    incremental_strategy='merge',
    unique_key=['sensor', 'product', 'acquired_at_utc', 'latitude', 'longitude'],
    partitioned_by=['month(acquired_at_utc)'],
    on_schema_change='append_new_columns'
) }}

{%- set since_day = incremental_since('acquired_at_utc', 10) %}

with typed as (
    select
        regexp_extract("$path", '/([A-Z0-9_]+)\.csv$', 1) as source,
        cast(latitude as double) as latitude,
        cast(longitude as double) as longitude,
        cast(frp as double) as frp_mw,
        lower(trim(confidence)) as confidence,
        cast(date_add('minute', cast(acq_time as integer) % 100,
            date_add('hour', cast(acq_time as integer) / 100,
                date_parse(acq_date, '%Y-%m-%d'))) as timestamp(6)) as acquired_at_utc,
        cast(date_parse(dt, '%Y-%m-%d') as date) as acq_date,
        daynight
    from {{ source('bronze', 'firms_fires') }}
    where latitude <> 'latitude'  -- guard against a repeated header line
        and dt >= '{{ since_day }}'
),

labelled as (
    select
        *,
        regexp_replace(source, '_(SP|NRT)$', '') as sensor,
        regexp_extract(source, '(SP|NRT)$', 1) as product
    from typed
),

deduped as (
    -- MERGE needs unique source rows; identical detections can repeat within a file.
    select *, row_number() over (
        partition by sensor, product, acquired_at_utc, latitude, longitude order by frp_mw desc
    ) as rn
    from labelled
)

select
    sensor, product, acquired_at_utc, acq_date, latitude, longitude, frp_mw, confidence, daynight
from deduped
where rn = 1

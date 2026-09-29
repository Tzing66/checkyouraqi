{#
  VIIRS fire detections, typed. When a day has both archive (SP) and near-real-time (NRT) files
  for a satellite, keep only SP. The two satellites mostly see the same fires, so downstream
  must not add their counts together. A view: the whole source is ~13 MB.
#}
{{ config(materialized='view') }}

with typed as (
    select
        regexp_extract("$path", '/([A-Z0-9_]+)\.csv$', 1) as source,
        cast(latitude as double) as latitude,
        cast(longitude as double) as longitude,
        cast(frp as double) as frp_mw,
        lower(trim(confidence)) as confidence,
        date_add('minute', cast(acq_time as integer) % 100,
            date_add('hour', cast(acq_time as integer) / 100,
                cast(date_parse(acq_date, '%Y-%m-%d') as timestamp))) as acquired_at_utc,
        cast(date_parse(dt, '%Y-%m-%d') as date) as acq_date,
        daynight
    from {{ source('bronze', 'firms_fires') }}
    where latitude <> 'latitude'  -- guard against a repeated header line
),

labelled as (
    select
        *,
        regexp_replace(source, '_(SP|NRT)$', '') as sensor,
        regexp_extract(source, '(SP|NRT)$', 1) as product
    from typed
),

ranked as (
    select
        *,
        dense_rank() over (
            partition by acq_date, sensor order by case product when 'SP' then 0 else 1 end
        ) as product_rank
    from labelled
)

select
    sensor, product, acquired_at_utc, acq_date, latitude, longitude, frp_mw, confidence, daynight
from ranked
where product_rank = 1

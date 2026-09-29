{# Calendar in IST (India's civil day), with festival flag. #}
{{ config(materialized='table') }}

select
    d as date_ist,
    year(d) as year,
    month(d) as month,
    day_of_week(d) as day_of_week,          -- 1 = Monday
    day_of_week(d) in (6, 7) as is_weekend,
    f.name is not null as is_festival,
    f.name as festival_name
from unnest(sequence(date '2025-01-01', date '2027-12-31', interval '1' day)) as t (d)
left join {{ ref('seed_festivals') }} as f on f.date = t.d

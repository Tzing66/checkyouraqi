{{ config(materialized='table') }}
select city_id, city_name, timezone from {{ ref('seed_cities') }}

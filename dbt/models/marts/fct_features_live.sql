{#
  Live features for the latest complete hour: one row per station and horizon, built with the
  SAME logic as training (macros/features.sql). When a station's recent PM2.5 is missing, its
  last known values are carried forward and input_age_hours / is_stale_input say how old they
  are (owner decision 2026-09-29).
#}
{{ config(materialized='table') }}

{{ features_sql('live') }}

{#
  Training features: one row per station, prediction time (every feature_issue_every_hours)
  and horizon (24/48/72h), leak-free by construction and by test
  (tests/assert_features_no_leakage.sql). Target is NULL when that hour isn't valid.
  All logic lives in macros/features.sql, shared with the live table.
#}
{{ config(materialized='table', partitioned_by=['month(issue_hour_start_utc)']) }}

{{ features_sql('train') }}

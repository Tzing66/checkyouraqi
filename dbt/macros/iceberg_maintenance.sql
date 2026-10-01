{#
  Housekeeping for incremental Iceberg tables. Every MERGE (hourly for some) adds data and
  metadata files and a snapshot; left alone, reads get slower and cost more S3 requests.
    OPTIMIZE ... REWRITE DATA USING BIN_PACK  compacts small data files
    VACUUM                                    expires snapshots older than 1 day (we set
                                              vacuum_max_snapshot_age_seconds; the Iceberg
                                              default is 5 days, and we don't need time travel)
                                              and deletes files no snapshot references
  Run daily from the dbt_daily DAG:  dbt run-operation iceberg_maintenance
#}
{% macro iceberg_maintenance() %}
  {%- if execute -%}
    {%- for node in graph.nodes.values()
          if node.resource_type == 'model'
          and node.config.materialized == 'incremental'
          and node.config.get('table_type', 'iceberg') == 'iceberg' -%}
      {%- set relation = node.schema ~ '.' ~ node.alias -%}
      {%- do run_query('alter table ' ~ relation ~ " set tblproperties "
                       ~ "('vacuum_max_snapshot_age_seconds'='86400')") -%}
      {%- do run_query('optimize ' ~ relation ~ ' rewrite data using bin_pack') -%}
      {%- do run_query('vacuum ' ~ relation) -%}
      {%- do log('maintained ' ~ relation, info=true) -%}
    {%- endfor -%}
  {%- endif -%}
{% endmacro %}

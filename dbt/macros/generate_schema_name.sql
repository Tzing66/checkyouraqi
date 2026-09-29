{# Use the configured schema (aqi_silver / aqi_gold / aqi_bronze) verbatim instead of dbt's
   default "<target>_<custom>" concatenation: the Glue databases are fixed by Terraform. #}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {{ custom_schema_name if custom_schema_name else target.schema }}
{%- endmacro %}

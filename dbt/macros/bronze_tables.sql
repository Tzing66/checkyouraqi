{#
  Bronze = the raw files in s3://<bucket>/bronze/, exposed to Athena as EXTERNAL tables in the
  aqi_bronze Glue database. Dropping one never deletes data.

    scripts/dbt.sh run-operation create_bronze_tables                      # create missing
    scripts/dbt.sh run-operation create_bronze_tables --args '{replace: true}'  # after a schema change

  Every file is one JSON document on one line (OpenX JSON SerDe, keys matched case-insensitively,
  so camelCase fields are declared lowercase; unknown keys are ignored). Partitions use
  partition projection, so there are no crawlers and no MSCK REPAIR. Nested structs only declare the
  fields we use.
#}

{% macro _bronze_json(name, prefix, columns, projection) %}
  {%- set bucket = env_var('DATA_BUCKET') -%}
  {%- set loc = 's3://' ~ bucket ~ '/bronze/' ~ prefix -%}
  create external table if not exists aqi_bronze.{{ name }} (
    {{ columns }}
  )
  partitioned by ({{ projection.partition_cols }})
  row format serde 'org.openx.data.jsonserde.JsonSerDe'
  with serdeproperties ('ignore.malformed.json' = 'false')
  location '{{ loc }}/'
  tblproperties (
    'projection.enabled' = 'true',
    {{ projection.props }},
    'storage.location.template' = '{{ loc }}/{{ projection.template }}'
  )
{% endmacro %}

{% macro _hourly_projection(start) %}
  {{ return({
      'partition_cols': 'dt string, hour string',
      'props': "'projection.dt.type' = 'date', 'projection.dt.format' = 'yyyy-MM-dd',
                'projection.dt.range' = '" ~ start ~ ",NOW', 'projection.dt.interval' = '1',
                'projection.dt.interval.unit' = 'DAYS',
                'projection.hour.type' = 'integer', 'projection.hour.range' = '0,23',
                'projection.hour.digits' = '2'",
      'template': 'dt=${dt}/hour=${hour}/'
  }) }}
{% endmacro %}

{% macro _daily_projection(start) %}
  {{ return({
      'partition_cols': 'dt string',
      'props': "'projection.dt.type' = 'date', 'projection.dt.format' = 'yyyy-MM-dd',
                'projection.dt.range' = '" ~ start ~ ",NOW', 'projection.dt.interval' = '1',
                'projection.dt.interval.unit' = 'DAYS'",
      'template': 'dt=${dt}/'
  }) }}
{% endmacro %}

{% macro _openaq_measurement() -%}
struct<
  `value`: double,
  parameter: struct<id: int, name: string, units: string>,
  period: struct<label: string, `interval`: string,
                 datetimefrom: struct<utc: string>, datetimeto: struct<utc: string>>,
  coverage: struct<expectedcount: int, observedcount: int,
                   percentcomplete: double, percentcoverage: double>,
  summary: struct<min: double, max: double, avg: double, sd: double>,
  flaginfo: struct<hasflags: boolean>
>
{%- endmacro %}

{% macro _openmeteo_response(hourly_fields) -%}
array<array<struct<
  latitude: double, longitude: double, elevation: double, utc_offset_seconds: int,
  hourly: struct<`time`: array<string>, {{ hourly_fields }}>
>>>
{%- endmacro %}

{% macro _weather_fields(suffixes=['']) -%}
  {%- set vars = ['temperature_2m', 'relative_humidity_2m', 'wind_speed_10m',
                  'wind_direction_10m', 'precipitation', 'surface_pressure'] -%}
  {%- set out = [] -%}
  {%- for s in suffixes -%}{%- for v in vars -%}
    {%- do out.append(v ~ s ~ ': array<double>') -%}
  {%- endfor -%}{%- endfor -%}
  {{ out | join(', ') }}
{%- endmacro %}

{% macro bronze_table_ddl() %}
  {%- set points = 'points array<struct<id: string, latitude: double, longitude: double>>' -%}
  {%- set weather = _weather_fields() ~ ', boundary_layer_height: array<double>' -%}
  {%- set previous = _weather_fields(['_previous_day1', '_previous_day2', '_previous_day3', '_previous_day4']) -%}
  {%- set aq = 'pm2_5: array<double>, pm10: array<double>' -%}
  {{ return({
    'openaq_pm25_hours': _bronze_json('openaq_pm25_hours', 'openaq/measurements',
        "logical_hour string, fetched_at string, window_start string, window_end string,
         stations map<string, array<struct<results: array<" ~ _openaq_measurement() ~ ">>>>,
         errors map<string, string>",
        _hourly_projection('2026-09-01')),
    'openaq_latest': _bronze_json('openaq_latest', 'openaq/latest',
        "logical_hour string, fetched_at string,
         stations map<string, array<struct<results: array<struct<
             `datetime`: struct<utc: string>, `value`: double,
             sensorsid: bigint, locationsid: bigint>>>>>,
         errors map<string, string>",
        _hourly_projection('2026-09-01')),
    'openaq_pm25_history': _bronze_json('openaq_pm25_history', 'openaq/measurements_history',
        "fetched_at string, location_id bigint, sensor_id bigint,
         window_start string, window_end string,
         pages array<struct<results: array<" ~ _openaq_measurement() ~ ">>>",
        {'partition_cols': 'month string',
         'props': "'projection.month.type' = 'date', 'projection.month.format' = 'yyyy-MM',
                   'projection.month.range' = '2025-01,NOW', 'projection.month.interval' = '1',
                   'projection.month.interval.unit' = 'MONTHS'",
         'template': 'month=${month}/'}),
    'openmeteo_forecasts': _bronze_json('openmeteo_forecasts', 'openmeteo/forecasts',
        "fetched_at string, forecast_issued_at string, logical_hour string, " ~ points ~ ",
         responses " ~ _openmeteo_response(weather),
        _hourly_projection('2026-09-01')),
    'openmeteo_air_quality_forecasts': _bronze_json('openmeteo_air_quality_forecasts',
        'openmeteo/air_quality_forecasts',
        "fetched_at string, forecast_issued_at string, logical_hour string, " ~ points ~ ",
         responses " ~ _openmeteo_response(aq),
        _hourly_projection('2026-09-01')),
    'openmeteo_actuals': _bronze_json('openmeteo_actuals', 'openmeteo/actuals',
        "fetched_at string, start_date string, end_date string, " ~ points ~ ",
         responses " ~ _openmeteo_response(weather),
        _daily_projection('2025-01-01')),
    'openmeteo_previous_runs': _bronze_json('openmeteo_previous_runs', 'openmeteo/previous_runs',
        "fetched_at string, start_date string, end_date string, " ~ points ~ ",
         responses " ~ _openmeteo_response(previous),
        _daily_projection('2025-01-01')),
    'openmeteo_air_quality': _bronze_json('openmeteo_air_quality', 'openmeteo/air_quality',
        "fetched_at string, start_date string, end_date string, " ~ points ~ ",
         responses " ~ _openmeteo_response(aq),
        _daily_projection('2025-01-01')),
  }) }}
{% endmacro %}

{% macro _firms_ddl() %}
  {%- set loc = 's3://' ~ env_var('DATA_BUCKET') ~ '/bronze/firms' -%}
  {#- NRT files have 14 columns, SP files a 15th (`type`); it reads as NULL for NRT.
      Everything is string here and typed in staging. -#}
  create external table if not exists aqi_bronze.firms_fires (
    latitude string, longitude string, bright_ti4 string, scan string, track string,
    acq_date string, acq_time string, satellite string, instrument string, confidence string,
    version string, bright_ti5 string, frp string, daynight string, `type` string
  )
  partitioned by (dt string)
  row format delimited fields terminated by ','
  location '{{ loc }}/'
  tblproperties (
    'skip.header.line.count' = '1',
    'projection.enabled' = 'true',
    'projection.dt.type' = 'date', 'projection.dt.format' = 'yyyy-MM-dd',
    'projection.dt.range' = '2025-01-01,NOW', 'projection.dt.interval' = '1',
    'projection.dt.interval.unit' = 'DAYS',
    'storage.location.template' = '{{ loc }}/dt=${dt}/'
  )
{% endmacro %}

{% macro create_bronze_tables(replace=false) %}
  {%- set ddl = bronze_table_ddl() -%}
  {%- do ddl.update({'firms_fires': _firms_ddl()}) -%}
  {%- for name, sql in ddl.items() -%}
    {%- if replace -%}
      {%- do run_query('drop table if exists aqi_bronze.' ~ name) -%}
    {%- endif -%}
    {%- do run_query(sql) -%}
    {%- do log('bronze table ready: aqi_bronze.' ~ name, info=true) -%}
  {%- endfor -%}
{% endmacro %}

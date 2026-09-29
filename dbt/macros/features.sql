{#
  Feature SQL shared by training (fct_features_hourly) and live prediction (fct_features_live),
  so the two can never drift apart. Everything uses only data available at issue time; the
  audit_* columns record each feature group's latest input time (tested for leakage).

  mode = 'train':
    - issue times every feature_issue_every_hours per station with enough recent data
    - missing hours stay missing (what the model was trained on); target joined
    - observed weather at issue time from ERA5; CAMS history lagged 12h
  mode = 'live' (owner decision 2026-09-29, docs/decisions.md):
    - one issue time = the latest complete hour, every (non-duplicate) station
    - PM2.5 inputs carry each station's LAST KNOWN values forward over gaps, and
      input_age_hours / is_stale_input say how old they are
    - observed weather from the latest forecast-API run (ERA5 lags ~5 days), CAMS from history
      or else the latest CAMS forecast; no target
  Weather for the target hour is the previous-runs forecast with days_before = h/24 + 1 in both.
#}
{% macro features_sql(mode) %}
{%- set every = var('feature_issue_every_hours') -%}
{%- set wlag = var('weather_publish_lag_hours') -%}
{%- set cams_lag = var('cams_lag_hours') -%}
{%- set horizons = var('forecast_horizons') -%}
{%- set live = (mode == 'live') -%}
{%- if mode not in ('train', 'live') -%}
  {{ exceptions.raise_compiler_error("features_sql mode must be 'train' or 'live'") }}
{%- endif %}

with base_hours as (
    select
        a.location_id,
        a.zone_id,
        a.city_id,
        a.hour_start_utc,
        a.pm25 as pm25_observed,
        z.pm25_median as zone_median_observed,
        c.pm25_median as city_median_observed,
        c.pm25_24h_median as city_24h_median_observed,
        {%- if live %}
        -- Carry the last known value forward over gaps (live only).
        coalesce(a.pm25, last_value(a.pm25) ignore nulls over w_all) as pm25,
        coalesce(z.pm25_median, last_value(z.pm25_median) ignore nulls over w_all)
            as zone_pm25_median,
        coalesce(c.pm25_median, last_value(c.pm25_median) ignore nulls over w_all)
            as city_pm25_median,
        coalesce(c.pm25_24h_median, last_value(c.pm25_24h_median) ignore nulls over w_all)
            as city_pm25_24h_median
        {%- else %}
        a.pm25,
        z.pm25_median as zone_pm25_median,
        c.pm25_median as city_pm25_median,
        c.pm25_24h_median as city_pm25_24h_median
        {%- endif %}
    from {{ ref('fct_aqi_hourly') }} as a
    left join {{ ref('agg_zone_hourly') }} as z
        on z.zone_id = a.zone_id and z.hour_start_utc = a.hour_start_utc
    left join {{ ref('agg_city_hourly') }} as c
        on c.city_id = a.city_id and c.hour_start_utc = a.hour_start_utc
    where not a.is_colocated_duplicate
    {%- if live %}
    window w_all as (partition by a.location_id order by a.hour_start_utc
                     rows between unbounded preceding and current row)
    {%- endif %}
),

station_hours as (
    select
        b.*,
        {%- if live %}
        avg(b.pm25) over (w rows between 23 preceding and current row) as pm25_24h,
        count(b.pm25) over (w rows between 23 preceding and current row) as valid_hours_24h,
        {%- else %}
        a.pm25_24h,
        a.valid_hours_24h,
        {%- endif %}
        lag(b.pm25, 1) over w as pm25_lag1,
        lag(b.pm25, 3) over w as pm25_lag3,
        lag(b.pm25, 6) over w as pm25_lag6,
        lag(b.pm25, 12) over w as pm25_lag12,
        lag(b.pm25, 24) over w as pm25_lag24,
        lag(b.pm25, 48) over w as pm25_lag48,
        avg(b.pm25) over (w rows between 5 preceding and current row) as pm25_mean_6h,
        max(b.pm25) over (w rows between 5 preceding and current row) as pm25_max_6h,
        avg(b.pm25) over (w rows between 23 preceding and current row) as pm25_mean_24h,
        max(b.pm25) over (w rows between 23 preceding and current row) as pm25_max_24h,
        -- Same hour-of-day over the previous 7 days (seasonal-naive baseline + feature).
        avg(case when b.pm25 is not null then b.pm25 end) over (
            partition by b.location_id, hour(b.hour_start_utc) order by b.hour_start_utc
            rows between 7 preceding and 1 preceding) as pm25_same_hour_7d,
        last_value(b.pm25) ignore nulls over (w rows between unbounded preceding and current row)
            as pm25_last_valid,
        -- Latest hour with a REAL reading (drives the staleness label in live mode).
        max(case when b.pm25_observed is not null then b.hour_start_utc end) over (
            w rows between unbounded preceding and current row) as last_valid_hour_utc
    from base_hours as b
    {%- if not live %}
    inner join {{ ref('fct_aqi_hourly') }} as a
        on a.location_id = b.location_id and a.hour_start_utc = b.hour_start_utc
    {%- endif %}
    window w as (partition by b.location_id order by b.hour_start_utc)
),

issues as (
    select *
    from station_hours
    {%- if live %}
    where hour_start_utc = (select max(hour_start_utc) from station_hours)
    {%- else %}
    where hour(hour_start_utc) % {{ every }} = 0
        and valid_hours_24h >= 6   -- enough recent data to say anything
    {%- endif %}
),

expanded as (
    select
        i.*,
        h.horizon_h,
        date_add('hour', 1, i.hour_start_utc) as issue_time_utc,
        date_add('hour', h.horizon_h, i.hour_start_utc) as target_hour_start_utc,
        -- Open-Meteo hours are on the hour; use the one inside the OpenAQ hour.
        date_add('minute', 30 + 60 * h.horizon_h, i.hour_start_utc) as target_weather_time_utc,
        h.horizon_h / 24 + 1 as weather_days_before
    from issues as i
    cross join unnest(array[{{ horizons | join(', ') }}]) as h (horizon_h)
),

{%- if live %}
latest_fc_run as (
    -- The newest forecast-API run available at issue time, per zone point.
    select f.point_id, f.valid_time_utc, f.issued_at, f.temperature_2m, f.relative_humidity_2m,
           f.wind_speed_10m, f.wind_direction_10m, f.boundary_layer_height,
           row_number() over (partition by f.point_id, f.valid_time_utc
                              order by f.issued_at desc) as rn
    from {{ ref('stg_openmeteo__weather_forecasts') }} as f
    where f.issued_at <= (select max(issue_time_utc) from expanded)
),

obs_weather as (
    select point_id, valid_time_utc as time_utc, temperature_2m, relative_humidity_2m,
           wind_speed_10m, wind_direction_10m, boundary_layer_height
    from latest_fc_run
    where rn = 1
),

cams as (
    select point_id, time_utc, cams_pm25 from {{ ref('stg_openmeteo__air_quality') }}
    union all
    -- CAMS history lags a few days; fall back to the latest CAMS forecast for that hour.
    select point_id, valid_time_utc as time_utc, cams_pm25
    from (
        select point_id, valid_time_utc, cams_pm25,
               row_number() over (partition by point_id, valid_time_utc
                                  order by issued_at desc) as rn
        from {{ ref('stg_openmeteo__air_quality_forecasts') }}
        where issued_at <= (select max(issue_time_utc) from expanded)
    )
    where rn = 1
),
{%- else %}
targets as (
    select location_id, hour_start_utc, pm25 as target_pm25
    from {{ ref('fct_aqi_hourly') }}
),

obs_weather as (
    select point_id, time_utc, temperature_2m, relative_humidity_2m, wind_speed_10m,
           wind_direction_10m, boundary_layer_height
    from {{ ref('stg_openmeteo__weather_actuals') }}
),

cams as (
    select point_id, time_utc, cams_pm25 from {{ ref('stg_openmeteo__air_quality') }}
),
{%- endif %}

fc_weather as (
    select point_id, time_utc, days_before, temperature_2m, relative_humidity_2m,
           wind_speed_10m, wind_direction_10m, precipitation, surface_pressure
    from {{ ref('stg_openmeteo__weather_previous_runs') }}
),

fires as (
    select * from {{ ref('int_fires_sector_rolling') }}
),

joined as (
    select
        e.*,
        {%- if live %}
        cast(null as double) as target_pm25,
        {%- else %}
        t.target_pm25,
        {%- endif %}
        ow.temperature_2m as obs_temperature_2m,
        ow.relative_humidity_2m as obs_relative_humidity_2m,
        ow.wind_speed_10m as obs_wind_speed_10m,
        ow.wind_direction_10m as obs_wind_direction_10m,
        ow.boundary_layer_height as obs_boundary_layer_height,
        ow.time_utc as obs_weather_time_utc,
        fw.temperature_2m as fc_temperature_2m,
        fw.relative_humidity_2m as fc_relative_humidity_2m,
        fw.wind_speed_10m as fc_wind_speed_10m,
        fw.wind_direction_10m as fc_wind_direction_10m,
        fw.precipitation as fc_precipitation,
        fw.surface_pressure as fc_surface_pressure,
        cm.cams_pm25 as cams_pm25_lagged,
        cm.time_utc as cams_time_utc,
        -- Sector the observed wind blows FROM (meteorological convention) = upwind.
        cast(floor(mod(ow.wind_direction_10m + 22.5, 360) / 45) as integer) as upwind_sector_index
    from expanded as e
    {%- if not live %}
    left join targets as t
        on t.location_id = e.location_id and t.hour_start_utc = e.target_hour_start_utc
    {%- endif %}
    left join obs_weather as ow
        on ow.point_id = e.zone_id
        and ow.time_utc = date_add('minute', 30, e.hour_start_utc)
    left join fc_weather as fw
        on fw.point_id = e.zone_id
        and fw.time_utc = e.target_weather_time_utc
        and fw.days_before = e.weather_days_before
    left join (
        {%- if live %}
        select point_id, time_utc, max(cams_pm25) as cams_pm25 from cams group by 1, 2
        {%- else %}
        select * from cams
        {%- endif %}
    ) as cm
        on cm.point_id = e.zone_id
        and cm.time_utc = date_add('hour', -{{ cams_lag }},
                                   date_add('minute', 30, e.hour_start_utc))
),

with_fires as (
    select
        j.*,
        f_nw.n_24h_arc as fires_nw_arc_24h,
        f_nw.n_72h_arc as fires_nw_arc_72h,
        f_nw.window_end_utc as fires_window_end_utc,
        f_up.n_24h_up as fires_upwind_24h,
        f_up.n_48h_up as fires_upwind_48h,
        f_up.n_72h_up as fires_upwind_72h,
        f_up.frp_24h_up as fires_upwind_frp_24h
    from joined as j
    left join (
        select as_of_utc, max(window_end_utc) as window_end_utc,
               sum(n_24h) as n_24h_arc, sum(n_72h) as n_72h_arc
        from fires where sector in ('N', 'NW', 'W')
        group by 1
    ) as f_nw on f_nw.as_of_utc = date_trunc('hour', j.issue_time_utc)
    left join (
        select ws.idx as wind_idx, f.as_of_utc,
               sum(f.n_24h) as n_24h_up, sum(f.n_48h) as n_48h_up,
               sum(f.n_72h) as n_72h_up, sum(f.frp_24h) as frp_24h_up
        from fires as f
        cross join unnest(sequence(0, 7)) as ws (idx)
        where f.sector_index in (ws.idx, (ws.idx + 1) % 8, (ws.idx + 7) % 8)
        group by 1, 2
    ) as f_up
        on f_up.wind_idx = j.upwind_sector_index
        and f_up.as_of_utc = date_trunc('hour', j.issue_time_utc)
)

select
    -- keys
    w.location_id,
    w.zone_id,
    w.city_id,
    w.horizon_h,
    w.hour_start_utc as issue_hour_start_utc,
    w.issue_time_utc,
    w.target_hour_start_utc,
    -- target (NULL when the target hour is not valid, and always in live mode)
    w.target_pm25,
    -- station history at issue time
    w.pm25 as pm25_lag0,
    w.pm25_lag1, w.pm25_lag3, w.pm25_lag6, w.pm25_lag12, w.pm25_lag24, w.pm25_lag48,
    w.pm25_mean_6h, w.pm25_max_6h, w.pm25_mean_24h, w.pm25_max_24h,
    w.pm25_24h, w.valid_hours_24h, w.pm25_same_hour_7d, w.pm25_last_valid,
    date_diff('hour', w.last_valid_hour_utc, w.hour_start_utc) as hours_since_last_valid,
    w.zone_pm25_median, w.city_pm25_median, w.city_pm25_24h_median,
    -- weather observed at issue time
    w.obs_temperature_2m, w.obs_relative_humidity_2m, w.obs_wind_speed_10m,
    w.obs_wind_direction_10m, w.obs_boundary_layer_height,
    -- weather at the target hour, as forecast >= (h/24+1) days earlier
    w.weather_days_before,
    w.fc_temperature_2m, w.fc_relative_humidity_2m, w.fc_wind_speed_10m,
    w.fc_wind_direction_10m, w.fc_precipitation, w.fc_surface_pressure,
    -- regional background and fires
    w.cams_pm25_lagged,
    coalesce(w.fires_upwind_24h, 0) as fires_upwind_24h,
    coalesce(w.fires_upwind_48h, 0) as fires_upwind_48h,
    coalesce(w.fires_upwind_72h, 0) as fires_upwind_72h,
    coalesce(w.fires_upwind_frp_24h, 0) as fires_upwind_frp_24h,
    coalesce(w.fires_nw_arc_24h, 0) as fires_nw_arc_24h,
    coalesce(w.fires_nw_arc_72h, 0) as fires_nw_arc_72h,
    -- calendar at the TARGET hour (known in advance), in IST
    hour(w.target_hour_start_utc + interval '5' hour + interval '30' minute) as target_hour_ist,
    day_of_week(w.target_hour_start_utc + interval '5' hour + interval '30' minute)
        as target_dow_ist,
    month(w.target_hour_start_utc + interval '5' hour + interval '30' minute) as target_month,
    coalesce(d.is_festival, false) as target_is_festival,
    coalesce(d.is_weekend, false) as target_is_weekend,
    {%- if live %}
    -- staleness of the PM2.5 inputs (live only): hours since the station's last REAL reading
    date_diff('hour', w.last_valid_hour_utc, w.hour_start_utc) as input_age_hours,
    coalesce(date_diff('hour', w.last_valid_hour_utc, w.hour_start_utc), 999999)
        > {{ var('freshness_live_max_hours') }} as is_stale_input,
    w.last_valid_hour_utc,
    {%- endif %}
    -- leakage audit: latest input time per feature group (must all be <= issue_time_utc)
    w.issue_time_utc as audit_station_obs_until,
    w.obs_weather_time_utc as audit_obs_weather_time,
    date_add('hour', {{ wlag }} - 24 * w.weather_days_before, w.target_weather_time_utc)
        as audit_weather_forecast_available_by,
    w.cams_time_utc as audit_cams_time,
    date_add('hour', {{ var('firms_publish_lag_hours') }}, w.fires_window_end_utc)
        as audit_fires_published_by
from with_fires as w
left join {{ ref('dim_date') }} as d
    on d.date_ist = cast(w.target_hour_start_utc + interval '5' hour + interval '30' minute
                         as date)
{% endmacro %}

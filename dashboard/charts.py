"""Altair charts, built to the dataviz skill's specs: 2px lines, legend + direct labels for
two series (none for one), recessive AQI reference lines, crosshair + tooltip on lines, one
y-axis per chart, and text in the theme's text colours, never in series colours or the
renderer's default (which is black, invisible on the dark theme)."""

from __future__ import annotations

import altair as alt
import pandas as pd

from dashboard.ui import AQI, BREAKPOINTS, SERIES

# Text tokens from the dataviz reference palette (secondary ink, per mode).
TEXT = {"light": "#52514e", "dark": "#c3c2b7"}


def _legend() -> alt.Legend:
    # Above the plot, so it never sits on top of the data.
    return alt.Legend(title=None, orient="top", direction="horizontal")


def _end_labels(last: pd.DataFrame, x, y, y_field: str, order: list[str], mode: str) -> alt.Chart:
    """Direct labels at each series' last point. When two series end close together they're
    nudged apart vertically (one up, one down) so they don't collide. `y` must be the SAME
    encoding object as the main layer's: Vega-Lite merges per-layer axes, and a differing one
    mangles or removes the shared axis."""
    layers = []
    values = dict(zip(last["series"], last[y_field], strict=False))
    close = (
        len(values) == 2
        and abs(list(values.values())[0] - list(values.values())[1])
        < max(abs(v) for v in values.values()) * 0.15
    )
    for name in order:
        part = last[last["series"] == name]
        if part.empty:
            continue
        # Higher-ending series goes up, the other down (only when they'd collide).
        dy = 0 if not close else (-10 if values[name] == max(values.values()) else 10)
        layers.append(
            alt.Chart(part)
            .mark_text(align="left", dx=8, dy=dy, fontSize=11, color=TEXT[mode])
            .encode(x=x, y=y, text="series:N")
        )
    return alt.layer(*layers) if layers else alt.Chart(last).mark_text()


def _aqi_axis(title: str) -> alt.Axis:
    """Y-axis whose ticks ARE the AQI category boundaries ("60 · Satisfactory"), with dotted
    gridlines there: the categories are readable without any in-plot text to collide with."""
    names = [label for label, _ in AQI.values()]
    expr = (
        " : ".join(f"datum.value == {b} ? '{b} · {names[i]}'" for i, b in enumerate(BREAKPOINTS))
        + " : datum.label"
    )
    return alt.Axis(
        title=title,
        values=[0, *BREAKPOINTS],
        labelExpr=expr,
        gridDash=[2, 3],
        labelOverlap=False,
        labelLimit=160,
    )


def history_chart(hourly: pd.DataFrame, mode: str = "light") -> alt.Chart:
    """Hourly PM2.5 and the CPCB 24h average for one station (two series, one unit)."""
    c1, c2 = SERIES[mode]
    order = ["Hourly PM2.5", "24h average"]
    d = hourly[["hour_start_utc", "pm25", "pm25_24h"]].copy()
    d["time_ist"] = d["hour_start_utc"].dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    long = d.melt(
        id_vars="time_ist", value_vars=["pm25", "pm25_24h"], var_name="series", value_name="value"
    )
    long["series"] = long["series"].map({"pm25": order[0], "pm25_24h": order[1]})
    color = alt.Color("series:N", scale=alt.Scale(domain=order, range=[c1, c2]), legend=_legend())
    x = alt.X("time_ist:T", title="Time (IST)")
    y = alt.Y("value:Q", axis=_aqi_axis("PM2.5 (µg/m³)"))
    lines = (
        alt.Chart(long)
        .mark_line(strokeWidth=2, interpolate="monotone")
        .encode(x=x, y=y, color=color)
    )

    hover = alt.selection_point(
        fields=["time_ist"], nearest=True, on="pointerover", empty=False, clear="pointerout"
    )
    wide = d.rename(columns={"pm25": "Hourly", "pm25_24h": "24h avg"})
    crosshair = (
        alt.Chart(wide)
        .mark_rule(strokeWidth=1, color=TEXT[mode])
        .encode(
            x="time_ist:T",
            opacity=alt.condition(hover, alt.value(0.5), alt.value(0)),
            tooltip=[
                alt.Tooltip("time_ist:T", title="Time (IST)", format="%d %b %H:%M"),
                alt.Tooltip("Hourly:Q", format=".0f", title="Hourly µg/m³"),
                alt.Tooltip("24h avg:Q", format=".0f", title="24h avg µg/m³"),
            ],
        )
        .add_params(hover)
    )
    points = (
        alt.Chart(long)
        .mark_point(size=64, filled=True)
        .encode(x=x, y=y, color=color, opacity=alt.condition(hover, alt.value(1), alt.value(0)))
        .transform_filter(hover)
    )

    last = long.dropna().sort_values("time_ist").groupby("series").tail(1)
    labels = _end_labels(last, x, y, "value", order, mode)
    return (
        alt.layer(lines, crosshair, points, labels)
        .properties(height=320, padding={"right": 90})
        .interactive(bind_y=False)
    )


def monthly_mae_chart(per_month: list[dict], mode: str = "light") -> alt.Chart:
    """Walk-forward MAE by test month: model vs persistence (two series, one unit)."""
    c1, c2 = SERIES[mode]
    order = ["This model", "No-change guess"]
    d = pd.DataFrame(per_month)
    d["month"] = pd.to_datetime(d["fold"] + "-01")
    long = d.melt(
        id_vars="month",
        value_vars=["mae", "persistence_mae"],
        var_name="series",
        value_name="mae_value",
    )
    long["series"] = long["series"].map({"mae": order[0], "persistence_mae": order[1]})
    color = alt.Color("series:N", scale=alt.Scale(domain=order, range=[c1, c2]), legend=_legend())
    x = alt.X("yearmonth(month):T", title="Test month")
    y = alt.Y("mae_value:Q", title="Typical error (µg/m³)")
    line = (
        alt.Chart(long)
        .mark_line(strokeWidth=2, point=alt.OverlayMarkDef(size=64))
        .encode(
            x=x,
            y=y,
            color=color,
            tooltip=[
                alt.Tooltip("yearmonth(month):T", title="Month"),
                alt.Tooltip("series:N", title="Predictor"),
                alt.Tooltip("mae_value:Q", format=".1f", title="MAE"),
            ],
        )
    )
    last = long.sort_values("month").groupby("series").tail(1)
    labels = _end_labels(last, x, y, "mae_value", order, mode)
    return alt.layer(line, labels).properties(height=300, padding={"right": 80})


FEATURE_NAMES = {
    "pm25_last_valid": "Latest reading",
    "pm25_same_hour_7d": "Same hour, past week",
    "location_id": "Which station",
    "fires_nw_arc_72h": "Upwind fires, last 3 days",
    "fires_nw_arc_24h": "Upwind fires, last day",
    "city_pm25_24h_median": "City 24h average",
    "city_pm25_median": "City average now",
    "fc_wind_speed_10m": "Forecast wind speed",
    "fc_wind_direction_10m": "Forecast wind direction",
    "pm25_mean_6h": "Average, last 6 hours",
    "target_month": "Month",
    "target_dow_ist": "Day of week",
    "target_hour_ist": "Hour of day",
}


def feature_label(name: str) -> str:
    return FEATURE_NAMES.get(name, name.replace("_", " ").capitalize())


def feature_bars(top: dict[str, float], mode: str = "light") -> alt.Chart:
    """Top features by share of gain (single series: no legend, the title names it)."""
    c1, _ = SERIES[mode]
    d = pd.DataFrame({"feature": [feature_label(k) for k in top], "share": list(top.values())})
    return (
        alt.Chart(d)
        .mark_bar(color=c1, cornerRadiusEnd=4, height=14)
        .encode(
            x=alt.X("share:Q", title="Share of decisions", axis=alt.Axis(format="%")),
            y=alt.Y("feature:N", sort="-x", title=None, axis=alt.Axis(labelLimit=240)),
            tooltip=[alt.Tooltip("feature:N"), alt.Tooltip("share:Q", format=".1%")],
        )
        .properties(height=28 * len(d))
    )


def forecast_vs_actual_chart(acc: pd.DataFrame, mode: str = "light") -> alt.Chart:
    c1, c2 = SERIES[mode]
    order = ["Forecast", "Actual"]
    d = acc.copy()
    d["time_ist"] = d["target_hour_start_utc"].dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    long = d.melt(
        id_vars=["time_ist", "horizon_h"],
        value_vars=["pm25_pred", "pm25_actual"],
        var_name="series",
        value_name="value",
    )
    long["series"] = long["series"].map({"pm25_pred": order[0], "pm25_actual": order[1]})
    color = alt.Color("series:N", scale=alt.Scale(domain=order, range=[c1, c2]), legend=_legend())
    x = alt.X("time_ist:T", title="Target time (IST)")
    y = alt.Y("value:Q", title="PM2.5 (µg/m³)")
    line = (
        alt.Chart(long)
        .mark_line(strokeWidth=2, point=True)
        .encode(
            x=x,
            y=y,
            color=color,
            tooltip=[
                "series:N",
                alt.Tooltip("time_ist:T", format="%d %b %H:%M"),
                alt.Tooltip("value:Q", format=".0f"),
            ],
        )
    )
    last = long.dropna().sort_values("time_ist").groupby("series").tail(1)
    return alt.layer(line, _end_labels(last, x, y, "value", order, mode)).properties(
        height=260, padding={"right": 70}
    )

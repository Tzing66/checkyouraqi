"""Altair charts, built to the dataviz skill's specs: 2px lines, legend + direct labels for
two series (none for one), recessive AQI reference lines, crosshair + tooltip on lines, one
y-axis per chart, and text in text colours (the theme's), never in series colours."""

from __future__ import annotations

import altair as alt
import pandas as pd

from dashboard.ui import AQI, BREAKPOINTS, SERIES


def _breakpoint_rules(y_max: float) -> alt.Chart:
    """Faint horizontal rules at the AQI category boundaries within view, labelled at right."""
    names = [label for label, _ in AQI.values()]
    rows = [{"y": b, "label": f"{names[i]} ≤ {b}"} for i, b in enumerate(BREAKPOINTS) if b < y_max]
    base = alt.Chart(pd.DataFrame(rows))
    rule = base.mark_rule(strokeDash=[2, 3], strokeWidth=1, opacity=0.35).encode(y="y:Q")
    text = base.mark_text(
        align="left", baseline="bottom", dx=4, dy=-2, fontSize=10, opacity=0.6
    ).encode(y="y:Q", x=alt.value(0), text="label:N")
    return rule + text


def history_chart(hourly: pd.DataFrame, mode: str = "light") -> alt.Chart:
    """Hourly PM2.5 and the CPCB 24h average for one station (two series, one unit)."""
    c1, c2 = SERIES[mode]
    d = hourly[["hour_start_utc", "pm25", "pm25_24h"]].copy()
    d["time_ist"] = d["hour_start_utc"].dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    long = d.melt(
        id_vars="time_ist", value_vars=["pm25", "pm25_24h"], var_name="series", value_name="value"
    )
    long["series"] = long["series"].map({"pm25": "Hourly PM2.5", "pm25_24h": "24h average"})
    color = alt.Color(
        "series:N",
        scale=alt.Scale(domain=["Hourly PM2.5", "24h average"], range=[c1, c2]),
        legend=alt.Legend(title=None, orient="top-left"),
    )
    x = alt.X("time_ist:T", title="Time (IST)")
    y = alt.Y("value:Q", title="PM2.5 (µg/m³)")
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
        .mark_rule(strokeWidth=1, opacity=0.5)
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
        .mark_point(size=40, filled=True)
        .encode(
            x=x,
            y=y,
            color=color,
            opacity=alt.condition(hover, alt.value(1), alt.value(0)),
        )
        .transform_filter(hover)
    )

    last = long.dropna().sort_values("time_ist").groupby("series").tail(1)
    labels = (
        alt.Chart(last).mark_text(align="left", dx=6, fontSize=11).encode(x=x, y=y, text="series:N")
    )

    y_max = float(long["value"].max() or 0) * 1.1
    return (
        (lines + _breakpoint_rules(y_max) + crosshair + points + labels)
        .properties(height=320)
        .interactive(bind_y=False)
    )


def monthly_mae_chart(per_month: list[dict], mode: str = "light") -> alt.Chart:
    """Walk-forward MAE by test month: model vs persistence (two series, one unit)."""
    c1, c2 = SERIES[mode]
    d = pd.DataFrame(per_month)
    d["month"] = pd.to_datetime(d["fold"] + "-01")
    long = d.melt(
        id_vars="month",
        value_vars=["mae", "persistence_mae"],
        var_name="series",
        value_name="mae_value",
    )
    long["series"] = long["series"].map({"mae": "LightGBM", "persistence_mae": "Persistence"})
    color = alt.Color(
        "series:N",
        scale=alt.Scale(domain=["LightGBM", "Persistence"], range=[c1, c2]),
        legend=alt.Legend(title=None, orient="top-right"),
    )
    x = alt.X("yearmonth(month):T", title="Test month")
    y = alt.Y("mae_value:Q", title="MAE (µg/m³, lower is better)")
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
    labels = (
        alt.Chart(last).mark_text(align="left", dx=8, fontSize=11).encode(x=x, y=y, text="series:N")
    )
    return (line + labels).properties(height=300)


def feature_bars(top: dict[str, float], mode: str = "light") -> alt.Chart:
    """Top features by share of gain (single series: no legend, the title names it)."""
    c1, _ = SERIES[mode]
    d = pd.DataFrame({"feature": list(top), "share": list(top.values())})
    return (
        alt.Chart(d)
        .mark_bar(color=c1, cornerRadiusEnd=4, height=14)
        .encode(
            x=alt.X("share:Q", title="Share of total gain", axis=alt.Axis(format="%")),
            y=alt.Y("feature:N", sort="-x", title=None),
            tooltip=[alt.Tooltip("feature:N"), alt.Tooltip("share:Q", format=".1%")],
        )
        .properties(height=28 * len(d))
    )


def forecast_vs_actual_chart(acc: pd.DataFrame, mode: str = "light") -> alt.Chart:
    c1, c2 = SERIES[mode]
    d = acc.copy()
    d["time_ist"] = d["target_hour_start_utc"].dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    long = d.melt(
        id_vars=["time_ist", "horizon_h"],
        value_vars=["pm25_pred", "pm25_actual"],
        var_name="series",
        value_name="value",
    )
    long["series"] = long["series"].map({"pm25_pred": "Forecast", "pm25_actual": "Actual"})
    color = alt.Color(
        "series:N",
        scale=alt.Scale(domain=["Forecast", "Actual"], range=[c1, c2]),
        legend=alt.Legend(title=None, orient="top-left"),
    )
    return (
        alt.Chart(long)
        .mark_line(strokeWidth=2, point=True)
        .encode(
            x=alt.X("time_ist:T", title="Target time (IST)"),
            y=alt.Y("value:Q", title="PM2.5 (µg/m³)"),
            color=color,
            tooltip=[
                "series:N",
                alt.Tooltip("time_ist:T", format="%d %b %H:%M"),
                alt.Tooltip("value:Q", format=".0f"),
            ],
        )
        .properties(height=260)
    )

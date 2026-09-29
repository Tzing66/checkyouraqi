"""Shared presentation helpers: AQI colours/labels, IST formatting, freshness wording.

AQI colours: India's official AQI hues, treated as a fixed *status* scale (never themed,
always shipped with the category label as text). Satisfactory and moderately-polluted are
darkened from the official #92D050 / yellow so every swatch clears 2:1 on light and dark
surfaces (validated with the dataviz skill's validator; see docs/decisions.md).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from ingestion.freshness import (
    FeedStatus,
    StationStatus,
    Thresholds,
    classify,
    feed_message,
    feed_report,
    format_ist,
    humanize_age,
    station_message,
)

AQI = {
    "good": ("Good", "#00B050"),
    "satisfactory": ("Satisfactory", "#7FBF3F"),
    "moderately_polluted": ("Moderately polluted", "#C9A800"),
    "poor": ("Poor", "#E68A00"),
    "very_poor": ("Very poor", "#FF0000"),
    "severe": ("Severe", "#A50021"),
}
NO_DATA = ("No data", "#8a8a85")
BREAKPOINTS = [30, 60, 90, 120, 250]  # upper bounds of the first five categories (µg/m³)

# Two-series charts (model vs baseline): categorical slots 1-2 of the dataviz palette.
SERIES = {
    "light": ("#2a78d6", "#eb6834"),
    "dark": ("#3987e5", "#d95926"),
}

STATUS_LABEL = {
    StationStatus.LIVE: ("Live", "🟢"),
    StationStatus.DELAYED: ("Delayed", "🟡"),
    StationStatus.INACTIVE: ("Inactive", "🟠"),
    StationStatus.OFFLINE: ("Offline", "⚫"),
    StationStatus.NO_DATA: ("No data", "⚪"),
}

THRESHOLDS = Thresholds.load()


def aqi_label(category: str | None) -> str:
    return AQI.get(category or "", NO_DATA)[0]


def aqi_color(category: str | None) -> str:
    return AQI.get(category or "", NO_DATA)[1]


def category_of(pm25: float | None) -> str | None:
    """India AQI PM2.5 category (CPCB rounding). Indicative for hourly values."""
    if pm25 is None or pd.isna(pm25):
        return None
    v = round(float(pm25))
    for (key, _), upper in zip(AQI.items(), BREAKPOINTS, strict=False):
        if v <= upper:
            return key
    return "severe"


def swatch(category: str | None) -> str:
    """Colour chip + text label (identity is never carried by colour alone)."""
    label, color = AQI.get(category or "", NO_DATA)
    return (
        f'<span style="display:inline-block;width:0.8em;height:0.8em;border-radius:3px;'
        f"background:{color};margin-right:0.35em;vertical-align:-0.05em;"
        f'border:1px solid rgba(0,0,0,0.35)"></span>{label}'
    )


def to_utc(ts) -> datetime | None:
    if ts is None or pd.isna(ts):
        return None
    t = pd.Timestamp(ts)
    return (t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")).to_pydatetime()


def ist(ts) -> str:
    t = to_utc(ts)
    return format_ist(t) if t else "—"


def age_text(ts, now: datetime | None = None) -> str:
    t = to_utc(ts)
    if not t:
        return "—"
    return humanize_age((now or datetime.now(UTC)) - t)


def station_state(last_reading, now: datetime | None = None) -> StationStatus:
    return classify(to_utc(last_reading), now or datetime.now(UTC), THRESHOLDS)


def feed_state(last_readings: dict[int, object], now: datetime | None = None):
    now = now or datetime.now(UTC)
    report = feed_report({k: to_utc(v) for k, v in last_readings.items()}, now, THRESHOLDS)
    return report, feed_message(report)


def station_banner(last_reading, feed, now: datetime | None = None) -> str | None:
    now = now or datetime.now(UTC)
    return station_message(station_state(last_reading, now), to_utc(last_reading), now, feed)


def status_badge(state: StationStatus) -> str:
    label, icon = STATUS_LABEL[state]
    return f"{icon} {label}"


__all__ = [
    "AQI",
    "BREAKPOINTS",
    "FeedStatus",
    "NO_DATA",
    "SERIES",
    "StationStatus",
    "age_text",
    "aqi_color",
    "aqi_label",
    "category_of",
    "feed_state",
    "ist",
    "station_banner",
    "station_state",
    "status_badge",
    "swatch",
    "to_utc",
]

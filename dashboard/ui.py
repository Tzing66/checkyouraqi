"""Shared presentation helpers: AQI colours/labels, IST formatting, freshness wording.

AQI colours: India's AQI hue order (green -> yellow -> orange -> red -> maroon), re-stepped
in lightness so neighbouring categories stay apart under colour-vision deficiency (min
adjacent OKLab dE 10.2 across protan/deutan/tritan). It's a fixed *status* scale: never
themed, always shipped with the category name as text. Pills use a pale tint of the colour,
which gives the pastel look; map markers use the full colour. See docs/decisions.md.
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
    "good": ("Good", "#66EAC8"),
    "satisfactory": ("Satisfactory", "#CFE353"),
    "moderately_polluted": ("Moderately polluted", "#DFAB47"),
    "poor": ("Poor", "#D0773C"),
    "very_poor": ("Very poor", "#C52A39"),
    "severe": ("Severe", "#6E2745"),
}
NO_DATA = ("No data", "#B4B9C6")

# Plain-language health note per category (after CPCB's National AQI health statements).
ADVICE = {
    "good": "Minimal health impact.",
    "satisfactory": "Minor breathing discomfort for sensitive people.",
    "moderately_polluted": "Discomfort for people with asthma, lung or heart disease, "
    "children and older adults.",
    "poor": "Breathing discomfort for most people on prolonged exposure.",
    "very_poor": "Respiratory illness on prolonged exposure. Limit time outdoors.",
    "severe": "Affects healthy people; serious for those with existing conditions. "
    "Stay indoors if you can.",
}
BREAKPOINTS = [30, 60, 90, 120, 250]  # upper bounds of the first five categories (µg/m³)

# Two-series charts (model vs baseline): categorical slots 1-2 of the dataviz palette.
SERIES = {
    "light": ("#5B6CD9", "#E07B4F"),
    "dark": ("#8B98EC", "#EE9A74"),
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


def _tint(hex_color: str, amount: float) -> str:
    """Mix a colour with white (amount = share of the colour)."""
    h = hex_color.lstrip("#")
    rgb = [int(h[i : i + 2], 16) for i in (0, 2, 4)]
    return "#" + "".join(f"{round(255 - (255 - c) * amount):02X}" for c in rgb)


def swatch(category: str | None) -> str:
    """AQI pill: pale tint of the category colour, a solid dot, and the category name as text
    (identity is never carried by colour alone)."""
    label, color = AQI.get(category or "", NO_DATA)
    return (
        f'<span class="cya-pill" style="background:{_tint(color, 0.22)};'
        f'border-color:{_tint(color, 0.45)}"><span class="cya-dot" '
        f'style="background:{color}"></span>{label}</span>'
    )


def advice(category: str | None) -> str:
    return ADVICE.get(category or "", "")


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
    "advice",
    "category_of",
    "feed_state",
    "ist",
    "station_banner",
    "station_state",
    "status_badge",
    "swatch",
    "to_utc",
]

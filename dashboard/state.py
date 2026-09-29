"""Streamlit-side state: cached snapshot, theme mode, the site-wide freshness banner."""

from __future__ import annotations

from datetime import UTC, datetime

import streamlit as st

from dashboard.data import Snapshot, default_source, load_snapshot, station_overview
from dashboard.ui import FeedStatus, feed_state, ist


@st.cache_resource(ttl=300, show_spinner="Loading the latest air-quality data…")
def get_snapshot() -> Snapshot:
    """The public snapshots, reloaded at most every 5 minutes (the export runs hourly)."""
    return load_snapshot(default_source())


@st.cache_data(ttl=300)
def get_station_overview(_snap: Snapshot, exported_at: str):  # exported_at keys the cache
    return station_overview(_snap)


def theme_mode() -> str:
    try:
        return "dark" if st.context.theme.type == "dark" else "light"
    except Exception:
        return "light"


def feed_banner(snap: Snapshot) -> None:
    """Site-wide banner: says when the *service* is down, vs individual stations (plan §1)."""
    status = snap.table("station_status")
    if status.empty:
        return
    last = dict(zip(status["location_id"], status["last_pm25_reading_utc"], strict=True))
    report, message = feed_state(last, datetime.now(UTC))
    if report.status == FeedStatus.OUTAGE:
        st.error(message, icon=":material/cloud_off:")
    elif report.status == FeedStatus.DEGRADED:
        st.warning(message, icon=":material/warning:")


def footer(snap: Snapshot) -> None:
    st.caption(
        f"Data exported {ist(snap.exported_at)}. Air quality: OpenAQ (CPCB stations). "
        "Weather & CAMS: Open-Meteo. Fires: NASA FIRMS. See About for attribution."
    )

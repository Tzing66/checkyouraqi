"""Streamlit-side state: cached snapshot, theme mode, the site-wide freshness notice, footer."""

from __future__ import annotations

from datetime import UTC, datetime

import streamlit as st

from dashboard.data import Snapshot, default_source, load_snapshot, station_overview
from dashboard.style import note
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


def feed_status(snap: Snapshot):
    """(report, message) for the whole data feed: the *service* being down, as opposed to
    individual stations (plan §1)."""
    status = snap.table("station_status")
    if status.empty:
        return None, None
    last = dict(zip(status["location_id"], status["last_pm25_reading_utc"], strict=True))
    return feed_state(last, datetime.now(UTC))


def feed_banner(snap: Snapshot) -> None:
    """One soft site-wide notice when the source is degraded or down; nothing when healthy."""
    report, message = feed_status(snap)
    if report is not None and report.status != FeedStatus.OK and message:
        note(message)


def footer(snap: Snapshot) -> None:
    st.markdown(
        f'<div class="cya-foot">Updated {ist(snap.exported_at)} · Air quality from OpenAQ '
        "(CPCB stations) · Weather from Open-Meteo · Fires from NASA FIRMS</div>",
        unsafe_allow_html=True,
    )

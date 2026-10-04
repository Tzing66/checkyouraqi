"""Station: the air now (honest about staleness), the next three days, the last 30 days, and
forecast vs actual once there's live data to compare."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard.charts import forecast_vs_actual_chart, history_chart
from dashboard.state import feed_status, footer, get_snapshot, get_station_overview, theme_mode
from dashboard.style import card, note
from dashboard.ui import (
    FeedStatus,
    advice,
    age_text,
    category_of,
    ist,
    station_banner,
    station_state,
    status_badge,
    swatch,
)

HORIZON_LABEL = {24: "Tomorrow", 48: "In 2 days", 72: "In 3 days"}


def hour_window(start_utc) -> str:
    """'30 Sep, 15:00–16:00 IST': the forecast is for a whole OpenAQ hour."""
    start = ist(start_utc)
    end = ist(pd.Timestamp(start_utc) + pd.Timedelta(hours=1)).split(", ")[1]
    return start.replace(" IST", f"–{end}")


snap = get_snapshot()
stations = get_station_overview(snap, str(snap.exported_at))
if stations.empty:
    st.error("No station data available.")
    st.stop()

ids = stations["location_id"].astype(int).tolist()
default = st.session_state.get("station_id", 235 if 235 in ids else ids[0])
choice = st.selectbox(
    "Choose a station",
    ids,
    index=ids.index(default) if default in ids else 0,
    format_func=lambda i: stations.loc[stations["location_id"] == i, "station_name"].iloc[0],
)
st.session_state["station_id"] = int(choice)
s = stations[stations["location_id"] == choice].iloc[0]

st.title(s.station_name)
st.markdown(
    f'<div class="cya-sub">{s.get("zone_name", s.zone_id)} · run by {s.agency or s.provider}</div>',
    unsafe_allow_html=True,
)

fc = snap.table("forecasts_latest")
fc = fc[fc["location_id"] == choice].sort_values("horizon_h") if not fc.empty else fc

# One notice, not a stack: the source being down explains both the old reading and the
# forecasts' stale inputs.
feed, feed_message = feed_status(snap)
if feed is not None and feed.status != FeedStatus.OK and feed_message:
    note(feed_message + " Forecasts below are built from the last known readings.")
else:
    message = station_banner(s.last_pm25_reading_utc, feed)
    if message:
        note(message)

# --- now ----------------------------------------------------------------------------------
state = station_state(s.last_pm25_reading_utc)
now_col, last_col = st.columns([3, 2], gap="medium")
with now_col:
    cat = s.aqi_category
    value = "—" if pd.isna(s.pm25_24h) else f"{s.pm25_24h:.0f}<small>µg/m³</small>"
    card(
        '<div class="cya-label">24-hour average</div>'
        f'<div class="cya-value">{value}</div>{swatch(cat)}'
        f'<div class="cya-advice">{advice(cat)}</div>'
    )
with last_col:
    v = s.last_pm25_value
    reading = "—" if pd.isna(v) else f"{v:.0f}<small>µg/m³</small>"
    card(
        '<div class="cya-label">Latest hourly reading</div>'
        f'<div class="cya-value-sm">{reading}</div>'
        f'<div class="cya-meta">{ist(s.last_pm25_reading_utc)} · '
        f"{age_text(s.last_pm25_reading_utc)}</div>"
        f'<div class="cya-meta">{status_badge(state)}</div>'
    )

# --- forecast -----------------------------------------------------------------------------
st.subheader("Next 3 days")
if fc.empty:
    st.caption("No forecast for this station yet (it has no usable history).")
else:
    cols = st.columns(len(fc), gap="medium")
    for col, r in zip(cols, fc.itertuples(index=False), strict=True):
        with col:
            card(
                f'<div class="cya-label">{HORIZON_LABEL.get(r.horizon_h, f"In {r.horizon_h}h")}'
                "</div>"
                f'<div class="cya-value-sm">{r.pm25_pred:.0f}<small>µg/m³</small></div>'
                f"{swatch(category_of(r.pm25_pred))}"
                f'<div class="cya-meta">{hour_window(r.target_hour_start_utc)}</div>'
            )
    st.caption(
        "Forecast for one hour, so the category is indicative (the official one uses a 24-hour "
        "average). See Forecast accuracy for how close past forecasts have been."
    )

# --- history ------------------------------------------------------------------------------
st.subheader("Last 30 days")
hist = snap.table("aqi_hourly_recent")
hist = hist[hist["location_id"] == choice].sort_values("hour_start_utc")
if hist.dropna(subset=["pm25"]).empty:
    st.caption("No valid readings in the last 30 days of data.")
else:
    with st.container(border=True):
        st.altair_chart(history_chart(hist, theme_mode()), width="stretch")
    st.caption(
        "Dotted lines mark the AQI category boundaries. Gaps are hours with missing or invalid "
        "readings."
    )
    with st.expander("See the numbers"):
        view = hist[["hour_start_utc", "pm25", "pm25_24h", "aqi_category"]].copy()
        view["hour_start_utc"] = view["hour_start_utc"].map(ist)
        st.dataframe(
            view.rename(
                columns={
                    "hour_start_utc": "Hour (IST)",
                    "pm25": "PM2.5",
                    "pm25_24h": "24h avg",
                    "aqi_category": "Category",
                }
            ),
            hide_index=True,
            width="stretch",
        )

# --- forecast vs actual (only once there is something to compare) ------------------------
acc = snap.table("forecast_accuracy_recent")
acc = acc[acc["location_id"] == choice] if not acc.empty else acc
if not acc.empty:
    st.subheader("How past forecasts did")
    h = st.segmented_control(
        "Forecast made", [24, 48, 72], default=24, format_func=lambda x: f"{x}h ahead"
    )
    with st.container(border=True):
        st.altair_chart(
            forecast_vs_actual_chart(acc[acc["horizon_h"] == h], theme_mode()), width="stretch"
        )

with st.expander("About this station"):
    st.markdown(
        f"- OpenAQ location **{int(s.location_id)}**, provider {s.provider}\n"
        + (
            f"- {s.pct_valid_hours:.0f}% of its hourly history is usable\n"
            if pd.notna(s.pct_valid_hours)
            else ""
        )
        + f"- Coordinates {s.latitude:.4f}, {s.longitude:.4f}"
    )

footer(snap)

"""Station: current state (with honest staleness), 24/48/72h forecast, 30-day history,
forecast vs actual."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard.charts import forecast_vs_actual_chart, history_chart
from dashboard.state import feed_banner, footer, get_snapshot, get_station_overview, theme_mode
from dashboard.ui import (
    age_text,
    category_of,
    feed_state,
    ist,
    station_banner,
    station_state,
    status_badge,
    swatch,
)


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
    "Station",
    ids,
    index=ids.index(default) if default in ids else 0,
    format_func=lambda i: stations.loc[stations["location_id"] == i, "station_name"].iloc[0],
)
st.session_state["station_id"] = int(choice)
s = stations[stations["location_id"] == choice].iloc[0]

st.title(s.station_name)
st.caption(
    f"{s.get('zone_name', s.zone_id)} · {s.agency or s.provider} · OpenAQ location "
    f"{int(s.location_id)}"
)
feed_banner(snap)

status = snap.table("station_status")
last = dict(zip(status["location_id"], status["last_pm25_reading_utc"], strict=True))
feed, _ = feed_state(last)
message = station_banner(s.last_pm25_reading_utc, feed)
state = station_state(s.last_pm25_reading_utc)
if message:
    st.warning(message, icon=":material/schedule:")

# --- current state ------------------------------------------------------------------------
c1, c2, c3 = st.columns(3)
with c1:
    v = s.last_pm25_value
    st.metric("Last hourly reading", "—" if pd.isna(v) else f"{v:.0f} µg/m³")
    st.caption(f"{ist(s.last_pm25_reading_utc)} ({age_text(s.last_pm25_reading_utc)})")
with c2:
    st.metric("24h average", "—" if pd.isna(s.pm25_24h) else f"{s.pm25_24h:.0f} µg/m³")
    st.markdown(swatch(s.aqi_category), unsafe_allow_html=True)
with c3:
    st.metric("Data status", status_badge(state))
    if pd.notna(s.pct_valid_hours):
        st.caption(f"{s.pct_valid_hours:.0f}% of this station's history is usable")

# --- forecast -----------------------------------------------------------------------------
st.subheader("Forecast")
fc = snap.table("forecasts_latest")
fc = fc[fc["location_id"] == choice].sort_values("horizon_h") if not fc.empty else fc
if fc.empty:
    st.info("No forecast for this station (it has no usable history yet).")
else:
    cols = st.columns(len(fc))
    for col, r in zip(cols, fc.itertuples(index=False), strict=True):
        with col:
            st.metric(
                f"In {r.horizon_h}h · {hour_window(r.target_hour_start_utc)}",
                f"{r.pm25_pred:.0f} µg/m³",
            )
            st.markdown(
                swatch(category_of(r.pm25_pred)) + " <small>(hourly, indicative)</small>",
                unsafe_allow_html=True,
            )
    stale = fc[fc["is_stale_input"]]
    if not stale.empty:
        r = stale.iloc[0]
        st.warning(
            f"These forecasts use this station's last known readings from "
            f"{ist(r.last_valid_hour_utc)} ({int(r.input_age_hours)} h old), because new "
            "readings aren't arriving. Treat them with caution.",
            icon=":material/warning:",
        )
    st.caption(
        f"Model {fc['model_version'].iloc[0]} · issued {ist(fc['issue_time_utc'].iloc[0])}"
        " · see Model health for how accurate these have been."
    )

# --- history ------------------------------------------------------------------------------
st.subheader("Last 30 days")
hist = snap.table("aqi_hourly_recent")
hist = hist[hist["location_id"] == choice].sort_values("hour_start_utc")
if hist.dropna(subset=["pm25"]).empty:
    st.info("No valid readings in the last 30 days of data.")
else:
    st.altair_chart(history_chart(hist, theme_mode()), use_container_width=True)
    st.caption(
        "Y-axis ticks and dotted lines mark India AQI category boundaries (official category uses "
        "the 24h average). Gaps are hours with missing or invalid readings."
    )
    with st.expander("Table view"):
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
            use_container_width=True,
        )

# --- forecast vs actual -------------------------------------------------------------------
st.subheader("Forecast vs actual")
acc = snap.table("forecast_accuracy_recent")
acc = acc[acc["location_id"] == choice] if not acc.empty else acc
if acc.empty:
    st.info(
        "Live forecast-vs-actual appears here once new readings arrive for hours we've "
        "forecast. Until then, see Model health for the historical backtest."
    )
else:
    h = st.segmented_control("Horizon", [24, 48, 72], default=24, format_func=lambda x: f"{x}h")
    st.altair_chart(
        forecast_vs_actual_chart(acc[acc["horizon_h"] == h], theme_mode()), use_container_width=True
    )

footer(snap)

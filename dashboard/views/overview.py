"""Overview: city and zone summary, station map, and "what's the air like where I am?"."""

from __future__ import annotations

import folium
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from dashboard.data import zone_summary
from dashboard.geo import geocode, nearest_stations
from dashboard.state import feed_banner, footer, get_snapshot, get_station_overview
from dashboard.ui import (
    AQI,
    age_text,
    aqi_color,
    aqi_label,
    category_of,
    ist,
    station_state,
    status_badge,
    swatch,
)

snap = get_snapshot()
stations = get_station_overview(snap, str(snap.exported_at))

st.title("Delhi NCR air quality")
feed_banner(snap)

# --- headline tiles -----------------------------------------------------------------------
city = snap.table("city_hourly_recent").dropna(subset=["pm25_24h_median"])
forecasts = snap.table("forecasts_latest")
c1, c2, c3 = st.columns(3)
with c1:
    if city.empty:
        st.metric("Delhi NCR · 24h average PM2.5", "—")
    else:
        row = city.sort_values("hour_start_utc").iloc[-1]
        st.metric("Delhi NCR · 24h average PM2.5", f"{row.pm25_24h_median:.0f} µg/m³")
        st.markdown(
            f"{swatch(category_of(row.pm25_24h_median))} · as of {ist(row.hour_start_utc)}",
            unsafe_allow_html=True,
        )
with c2:
    live = sum(station_state(t).value == "live" for t in stations["last_pm25_reading_utc"])
    st.metric("Stations reporting live", f"{live} / {len(stations)}")
    newest = stations["last_pm25_reading_utc"].max()
    st.caption(f"Newest reading {ist(newest)} ({age_text(newest)})")
with c3:
    if forecasts.empty:
        st.metric("Tomorrow (city median forecast)", "—")
    else:
        f24 = forecasts[forecasts["horizon_h"] == 24]
        med = float(f24["pm25_pred"].median())
        st.metric("Tomorrow · median station forecast", f"{med:.0f} µg/m³")
        if f24["is_stale_input"].any():
            st.caption("⚠️ Built from last known readings (source not updating).")

# --- map + nearest-station lookup ---------------------------------------------------------
left, right = st.columns([3, 2], gap="large")

with left:
    st.subheader("Stations")
    # OpenStreetMap standard tiles (free with attribution; CARTO basemaps now need a key).
    m = folium.Map(
        location=[28.61, 77.21], zoom_start=10, tiles="OpenStreetMap", control_scale=True
    )
    for r in stations.itertuples(index=False):
        state = station_state(r.last_pm25_reading_utc)
        stale = state.value not in ("live", "delayed")
        tip = (
            f"<b>{r.station_name}</b><br>"
            f"{aqi_label(r.aqi_category)}"
            + (f" · 24h avg {r.pm25_24h:.0f} µg/m³" if pd.notna(r.pm25_24h) else "")
            + f"<br>Last reading {ist(r.last_pm25_reading_utc)} "
            f"({age_text(r.last_pm25_reading_utc)})" + f"<br>{status_badge(state)}"
        )
        folium.CircleMarker(
            location=[r.latitude, r.longitude],
            radius=8,
            color="#1a1a19",
            weight=1.5,
            dash_array="3 3" if stale else None,
            fill=True,
            fill_color=aqi_color(r.aqi_category),
            fill_opacity=0.9,
            tooltip=folium.Tooltip(tip),
        ).add_to(m)
    clicked = st_folium(
        m,
        height=500,
        use_container_width=True,
        key="station_map",
        returned_objects=["last_object_clicked", "last_clicked"],
    )
    legend = " &nbsp; ".join(swatch(k) for k in AQI) + f" &nbsp; {swatch(None)}"
    st.markdown(
        f"<small>24h-average AQI category (India, PM2.5): {legend}<br>"
        "Dashed outline = station not reporting live. Click the map to find the nearest "
        "stations.</small>",
        unsafe_allow_html=True,
    )

with right:
    st.subheader("What's the air like where I am?")
    point = None
    with st.form("locality"):
        q = st.text_input("Locality in Delhi NCR", placeholder="e.g. Dwarka Sector 10, Noida 62")
        if st.form_submit_button("Find nearest stations") and q:
            place = st.cache_data(ttl=86400, show_spinner="Searching…")(geocode)(q)
            if place is None:
                st.warning("No match in Delhi NCR. Try a nearby landmark or sector.")
            else:
                point = (place.latitude, place.longitude, place.name)
    with st.expander("Or enter coordinates"):
        lat = st.number_input("Latitude", 28.30, 28.95, 28.6139, format="%.4f")
        lon = st.number_input("Longitude", 76.80, 77.65, 77.2090, format="%.4f")
        if st.button("Use these coordinates"):
            point = (lat, lon, f"{lat:.4f}, {lon:.4f}")
    if clicked and clicked.get("last_clicked") and point is None:
        c = clicked["last_clicked"]
        point = (c["lat"], c["lng"], f"map point {c['lat']:.3f}, {c['lng']:.3f}")
    if point:
        st.session_state["near_point"] = point
    point = st.session_state.get("near_point")

    if point:
        lat, lon, name = point
        st.caption(f"Nearest to **{name}**")
        for r in nearest_stations(stations, lat, lon).itertuples(index=False):
            state = station_state(r.last_pm25_reading_utc)
            st.markdown(
                f"**{r.station_name}** · {r.distance_km} km<br>"
                f"{swatch(r.aqi_category)}"
                + (f" · {r.pm25_24h:.0f} µg/m³ (24h)" if pd.notna(r.pm25_24h) else "")
                + f"<br><small>{status_badge(state)} · last reading "
                f"{ist(r.last_pm25_reading_utc)} ({age_text(r.last_pm25_reading_utc)})</small>",
                unsafe_allow_html=True,
            )
            if st.button("Open station", key=f"open_{r.location_id}"):
                st.session_state["station_id"] = int(r.location_id)
                st.switch_page("views/station.py")
    st.caption("Locality search © OpenStreetMap contributors (Nominatim).")

# --- zones --------------------------------------------------------------------------------
st.subheader("Zones")
zones = snap.table("zone_hourly_recent").dropna(subset=["pm25_24h_median"])
if zones.empty:
    st.info("No zone averages available yet.")
else:
    z = zone_summary(snap)
    rows = "".join(
        f"<tr><td>{r.zone_name}</td><td style='text-align:right'>{r.pm25_24h_median:.0f}</td>"
        f"<td>{swatch(category_of(r.pm25_24h_median))}</td>"
        f"<td style='text-align:right'>"
        f"{int(r.stations_with_24h_avg)}/{int(r.stations_expected)}</td>"
        f"<td>{ist(r.hour_start_utc)}</td></tr>"
        for r in z.itertuples(index=False)
    )
    st.markdown(
        "<table style='width:100%'><thead><tr><th>Zone</th><th style='text-align:right'>"
        "24h avg µg/m³</th><th>Category</th><th style='text-align:right'>Stations</th>"
        f"<th>As of</th></tr></thead><tbody>{rows}</tbody></table>",
        unsafe_allow_html=True,
    )
    st.caption(
        "Zone value = median of its stations' 24h averages (one faulty station can't "
        "distort it); co-located duplicates excluded."
    )

footer(snap)

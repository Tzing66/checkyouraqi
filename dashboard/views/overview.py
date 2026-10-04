"""Home: how's the air now, what about tomorrow, what's it like near me, then the map and
areas. Details live on the Stations and Forecast accuracy pages."""

from __future__ import annotations

import folium
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from dashboard.data import zone_summary
from dashboard.geo import geocode, nearest_stations
from dashboard.state import feed_banner, footer, get_snapshot, get_station_overview
from dashboard.style import card
from dashboard.ui import (
    AQI,
    advice,
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
st.markdown(
    '<div class="cya-sub">Fine-particle pollution (PM2.5) from 70 monitoring stations, with '
    "forecasts up to 3 days ahead.</div>",
    unsafe_allow_html=True,
)
feed_banner(snap)

# --- now and tomorrow ---------------------------------------------------------------------
city = snap.table("city_hourly_recent").dropna(subset=["pm25_24h_median"])
forecasts = snap.table("forecasts_latest")
now_col, tomorrow_col = st.columns(2, gap="medium")

with now_col:
    if city.empty:
        card('<div class="cya-label">Right now</div><div class="cya-value">—</div>')
    else:
        row = city.sort_values("hour_start_utc").iloc[-1]
        cat = category_of(row.pm25_24h_median)
        card(
            '<div class="cya-label">Right now · city average</div>'
            f'<div class="cya-value">{row.pm25_24h_median:.0f}<small>µg/m³</small></div>'
            f"{swatch(cat)}"
            f'<div class="cya-advice">{advice(cat)}</div>'
            f'<div class="cya-meta">24-hour average as of {ist(row.hour_start_utc)}</div>'
        )

with tomorrow_col:
    f24 = forecasts[forecasts["horizon_h"] == 24] if not forecasts.empty else forecasts
    if f24.empty:
        card('<div class="cya-label">Tomorrow</div><div class="cya-value">—</div>')
    else:
        med = float(f24["pm25_pred"].median())
        cat = category_of(med)
        target = ist(f24["target_hour_start_utc"].iloc[0])
        stale = " · from last known readings" if bool(f24["is_stale_input"].any()) else ""
        card(
            '<div class="cya-label">Tomorrow · forecast</div>'
            f'<div class="cya-value">{med:.0f}<small>µg/m³</small></div>'
            f"{swatch(cat)}"
            f'<div class="cya-advice">{advice(cat)}</div>'
            f'<div class="cya-meta">Typical station, {target}{stale}</div>'
        )

# --- near me ------------------------------------------------------------------------------
st.subheader("Check your area")
with st.form("locality", border=False):
    q_col, b_col = st.columns([5, 1], vertical_alignment="bottom")
    q = q_col.text_input(
        "Locality",
        placeholder="Try “Dwarka Sector 10” or “Noida 62”",
        label_visibility="collapsed",
    )
    submitted = b_col.form_submit_button("Search", type="primary", width="stretch")

point = None
if submitted and q:
    place = st.cache_data(ttl=86400, show_spinner="Searching…")(geocode)(q)
    if place is None:
        st.warning("No match in Delhi NCR. Try a nearby landmark or sector.")
    else:
        point = (place.latitude, place.longitude, place.name)

# --- map ----------------------------------------------------------------------------------
m = folium.Map(location=[28.61, 77.21], zoom_start=10, tiles="OpenStreetMap", control_scale=True)
# Muted base map so the station colours carry the page (OSM tiles; attribution kept).
m.get_root().header.add_child(
    folium.Element(
        "<style>.leaflet-tile-pane{filter:saturate(0.25) brightness(1.06) contrast(0.92)}</style>"
    )
)
for r in stations.itertuples(index=False):
    state = station_state(r.last_pm25_reading_utc)
    stale = state.value not in ("live", "delayed")
    tip = (
        f"<b>{r.station_name}</b><br>{aqi_label(r.aqi_category)}"
        + (f" · {r.pm25_24h:.0f} µg/m³ (24h)" if pd.notna(r.pm25_24h) else "")
        + f"<br>{status_badge(state)} · {age_text(r.last_pm25_reading_utc)}"
    )
    folium.CircleMarker(
        location=[r.latitude, r.longitude],
        radius=9,
        color="#6B7180" if stale else "#FFFFFF",
        weight=2,
        dash_array="3 3" if stale else None,
        fill=True,
        fill_color=aqi_color(r.aqi_category),
        fill_opacity=0.95,
        tooltip=folium.Tooltip(tip),
    ).add_to(m)

map_col, near_col = st.columns([3, 2], gap="large")
with map_col:
    clicked = st_folium(
        m,
        height=460,
        use_container_width=True,
        key="station_map",
        returned_objects=["last_clicked"],
    )
    legend = "".join(swatch(k) for k in AQI) + swatch(None)
    st.markdown(f'<div class="cya-legend">{legend}</div>', unsafe_allow_html=True)
    st.caption(
        "24-hour average. Dashed outline: station not reporting live. Tap the map to see the "
        "nearest stations."
    )

if clicked and clicked.get("last_clicked") and point is None:
    c = clicked["last_clicked"]
    point = (c["lat"], c["lng"], "the point you tapped")
if point:
    st.session_state["near_point"] = point
point = st.session_state.get("near_point")

with near_col:
    if not point:
        card(
            '<div class="cya-label">Nearest stations</div>'
            '<div class="cya-advice">Search for a locality above, or tap the map, to see the '
            "closest monitoring stations.</div>"
        )
    else:
        lat, lon, name = point
        st.markdown(f"**Nearest to {name}**")
        for r in nearest_stations(stations, lat, lon).itertuples(index=False):
            with st.container(border=True):
                value = f"{r.pm25_24h:.0f} µg/m³" if pd.notna(r.pm25_24h) else "No recent data"
                st.markdown(
                    f'<div class="cya-zone"><span class="cya-zone-name">{r.station_name}</span>'
                    f'<span class="cya-meta" style="margin:0">{r.distance_km} km</span></div>'
                    f'<div class="cya-zone"><span>{value}</span>{swatch(r.aqi_category)}</div>',
                    unsafe_allow_html=True,
                )
                if st.button("View station →", key=f"open_{r.location_id}", type="tertiary"):
                    st.session_state["station_id"] = int(r.location_id)
                    st.switch_page("views/station.py")
        st.caption("Locality search © OpenStreetMap contributors (Nominatim).")

# --- areas --------------------------------------------------------------------------------
st.subheader("By area")
z = zone_summary(snap) if not snap.table("zone_hourly_recent").empty else pd.DataFrame()
if z.empty:
    st.caption("No area averages yet.")
else:
    cols = st.columns(3, gap="small")
    for i, r in enumerate(z.itertuples(index=False)):
        with cols[i % 3]:
            card(
                f'<div class="cya-zone-name">{r.zone_name}</div>'
                f'<div class="cya-value-sm">{r.pm25_24h_median:.0f}<small>µg/m³</small></div>'
                f"{swatch(category_of(r.pm25_24h_median))}"
            )
            st.write("")
    st.caption("Median of each area's stations, 24-hour average.")

footer(snap)

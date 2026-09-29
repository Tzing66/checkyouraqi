"""About: attribution, method, caveats."""

import streamlit as st

from dashboard.state import footer, get_snapshot

snap = get_snapshot()
st.title("About CheckYourAQI")

st.markdown("""
Air-quality forecasts for Delhi NCR: 70 government monitoring stations, grouped into nine
zones, with PM2.5 forecasts 24, 48 and 72 hours ahead.

### Data sources and attribution
- **Air quality:** [OpenAQ](https://openaq.org), from the Central Pollution Control Board
  (CPCB) network and state boards (DPCC, UPPCB, HSPCB), IMD and IITM.
- **Weather and CAMS air-quality model:** [Open-Meteo](https://open-meteo.com) (CC BY 4.0),
  including ECMWF/Copernicus data.
- **Fires:** NASA FIRMS VIIRS active-fire detections (NASA LANCE / FIRMS).
- **Map tiles and locality search:** © OpenStreetMap contributors (tile.openstreetmap.org
  and Nominatim).

### How it works
- Hourly ingestion into an S3 lakehouse; dbt on Athena cleans, grids and aggregates it.
- The forecast model (LightGBM, one per horizon) uses recent PM2.5, weather *forecasts as they
  were published at prediction time*, CAMS, upwind crop fires and the calendar. It's validated
  walk-forward so it's never tested on data it trained on.
- The AQI category shown is India's PM2.5 scale on the 24-hour average. Colours follow the
  official scale, and every colour always comes with its category name.

### Caveats
- **Stale data is shown, never hidden.** When a station, or the whole data service, stops
  sending readings, pages show the last reading with its time and say why it's old.
  Forecasts made from stale readings are labelled.
- Forecast history starts Feb 2025 (all OpenAQ has for these stations), so the model has
  seen only one stubble-burning season.
- Forecasts are indicative and not official advisories. Check CPCB/SAFAR for official ones.
""")
footer(snap)

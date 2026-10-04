"""CheckYourAQI dashboard (Streamlit). Reads only the public/ Parquet snapshots.

PYTHONPATH=. uv run streamlit run dashboard/app.py
"""

import sys
from pathlib import Path

import streamlit as st

# `streamlit run dashboard/app.py` puts dashboard/ on sys.path, not the repo root, so the
# project's packages (dashboard, ingestion) wouldn't import on Streamlit Cloud without this.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

st.set_page_config(
    page_title="CheckYourAQI · Delhi NCR air quality",
    page_icon=":material/air:",
    layout="wide",
)

from dashboard.style import apply  # noqa: E402  (needs the sys.path fix above)

apply()

pages = [
    st.Page("views/overview.py", title="Home", icon=":material/home:", default=True),
    st.Page("views/station.py", title="Stations", icon=":material/location_on:"),
    st.Page("views/model_health.py", title="Forecast accuracy", icon=":material/insights:"),
    st.Page("views/about.py", title="About", icon=":material/info:"),
]
st.navigation(pages, position="top").run()

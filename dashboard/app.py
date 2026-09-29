"""CheckYourAQI dashboard (Streamlit). Reads only the public/ Parquet snapshots.

PYTHONPATH=. uv run streamlit run dashboard/app.py
"""

import streamlit as st

st.set_page_config(
    page_title="CheckYourAQI · Delhi NCR air quality",
    page_icon=":material/air:",
    layout="wide",
)

pages = [
    st.Page("views/overview.py", title="Overview", icon=":material/map:", default=True),
    st.Page("views/station.py", title="Station", icon=":material/location_on:"),
    st.Page("views/model_health.py", title="Model health", icon=":material/monitoring:"),
    st.Page("views/about.py", title="About", icon=":material/info:"),
]
st.navigation(pages).run()

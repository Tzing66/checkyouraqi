""" "What's the air like where I am?": nearest stations to a point, and locality search.

Locality search uses OpenStreetMap Nominatim under its usage policy: an identifying
User-Agent, <= 1 request/second (the UI caches results and only searches on submit), results
restricted to the Delhi NCR box, and "© OpenStreetMap contributors" attribution on the page.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx
import pandas as pd

from ingestion.openaq.stations import haversine_km

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "CheckYourAQI/0.1 (portfolio project; https://github.com/Tzing66/checkyouraqi)"
# Delhi NCR search box, slightly wider than the station bbox: lon_min, lat_max, lon_max, lat_min
VIEWBOX = "76.70,29.00,77.75,28.20"
_last_call = 0.0


@dataclass(frozen=True)
class Place:
    name: str
    latitude: float
    longitude: float


def nearest_stations(stations: pd.DataFrame, lat: float, lon: float, n: int = 3) -> pd.DataFrame:
    """Stations sorted by great-circle distance from (lat, lon), with distance_km."""
    if stations.empty:
        return stations
    d = stations.copy()
    d["distance_km"] = [
        round(haversine_km((lat, lon), (r.latitude, r.longitude)), 1)
        for r in d.itertuples(index=False)
    ]
    return d.sort_values("distance_km").head(n).reset_index(drop=True)


def geocode(query: str, *, client: httpx.Client | None = None) -> Place | None:
    """First Nominatim match for `query` inside Delhi NCR, or None."""
    global _last_call
    query = query.strip()
    if not query:
        return None
    wait = 1.0 - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)  # Nominatim policy: max 1 request per second
    _last_call = time.monotonic()
    http = client or httpx.Client(timeout=15)
    resp = http.get(
        NOMINATIM_URL,
        params={
            "q": query,
            "format": "jsonv2",
            "limit": 1,
            "countrycodes": "in",
            "viewbox": VIEWBOX,
            "bounded": 1,
        },
        headers={"User-Agent": USER_AGENT, "Accept-Language": "en"},
    )
    resp.raise_for_status()
    hits = resp.json()
    if not hits:
        return None
    h = hits[0]
    return Place(h.get("display_name", query), float(h["lat"]), float(h["lon"]))

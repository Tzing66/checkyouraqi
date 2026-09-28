"""Weather points: one per zone, at the centroid of its (non-co-located) stations.

Open-Meteo's global models are ~10-25 km grids, so stations in one zone mostly share a cell;
per-zone points carry the same signal as per-station ones at a fraction of the API budget.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from ingestion.openmeteo.models import WeatherPoint
from ingestion.settings import CONFIG_DIR


def zone_points(config_dir: Path = CONFIG_DIR) -> list[WeatherPoint]:
    stations = {
        s["location_id"]: s
        for s in yaml.safe_load((config_dir / "stations.yaml").read_text())["stations"]
    }
    points = []
    for zone in yaml.safe_load((config_dir / "zones.yaml").read_text())["zones"]:
        members = [
            stations[sid]
            for sid in zone["stations"]
            if sid in stations and not stations[sid].get("colocated_with")
        ]
        if not members:
            continue
        lat = sum(s["latitude"] for s in members) / len(members)
        lon = sum(s["longitude"] for s in members) / len(members)
        points.append(WeatherPoint(zone["id"], round(lat, 4), round(lon, 4)))
    return points

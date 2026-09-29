"""Generate dbt seeds from config/ (the single source of truth). Rerun after changing config.

    PYTHONPATH=. uv run python scripts/export_seeds.py
tests/test_config.py fails if the committed seeds drift from config.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

import yaml

from ingestion.openmeteo.points import zone_points
from ingestion.settings import CONFIG_DIR, ROOT

SEEDS_DIR = ROOT / "dbt" / "seeds"


def _csv(header: list[str], rows: list[list[object]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(["" if v is None else v for v in r] for r in rows)
    return buf.getvalue()


def build(config_dir: Path = CONFIG_DIR) -> dict[str, str]:
    stations = yaml.safe_load((config_dir / "stations.yaml").read_text())
    zones = yaml.safe_load((config_dir / "zones.yaml").read_text())
    cities = yaml.safe_load((config_dir / "cities.yaml").read_text())["cities"]
    bp = yaml.safe_load((config_dir / "aqi_breakpoints.yaml").read_text())
    zone_of = {sid: z for z in zones["zones"] for sid in z["stations"]}

    return {
        "seed_stations.csv": _csv(
            [
                "location_id",
                "station_name",
                "agency",
                "provider",
                "latitude",
                "longitude",
                "pm25_sensor_id",
                "city_id",
                "zone_id",
                "colocated_with",
                "first_seen_utc",
            ],
            [
                [
                    s["location_id"],
                    s["name"],
                    s["agency"],
                    s["provider"],
                    s["latitude"],
                    s["longitude"],
                    s["pm25_sensor_id"],
                    stations["city_id"],
                    zone_of[s["location_id"]]["id"],
                    s["colocated_with"],
                    s["first_seen_utc"],
                ]
                for s in stations["stations"]
            ],
        ),
        "seed_station_sensors.csv": _csv(
            ["location_id", "parameter", "sensor_id"],
            [
                [s["location_id"], param, sid]
                for s in stations["stations"]
                for param, sid in s["sensors"].items()
            ],
        ),
        "seed_zones.csv": _csv(
            ["zone_id", "zone_name", "city_id"],
            [[z["id"], z["name"], zones["city_id"]] for z in zones["zones"]],
        ),
        "seed_cities.csv": _csv(
            ["city_id", "city_name", "timezone"],
            [[c["id"], c["name"], c["timezone"]] for c in cities],
        ),
        "seed_weather_points.csv": _csv(
            ["point_id", "latitude", "longitude"],
            [[p.id, p.latitude, p.longitude] for p in zone_points(config_dir)],
        ),
        "seed_aqi_breakpoints.csv": _csv(
            ["category", "category_order", "pm25_low", "pm25_high", "aqi_low", "aqi_high"],
            [
                [
                    c["name"],
                    i,
                    c["concentration"][0],
                    c["concentration"][1],
                    c["aqi"][0],
                    c["aqi"][1],
                ]
                for i, c in enumerate(bp["categories"], 1)
            ],
        ),
        "seed_festivals.csv": (config_dir / "festivals.csv").read_text(),
    }


def main() -> None:
    SEEDS_DIR.mkdir(parents=True, exist_ok=True)
    for name, text in build().items():
        (SEEDS_DIR / name).write_text(text)
        print("wrote", SEEDS_DIR.relative_to(ROOT) / name)


if __name__ == "__main__":
    main()

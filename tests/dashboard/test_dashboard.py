import io
import json

import httpx
import pandas as pd
import pytest

from dashboard.data import MANIFEST_KEY, load_snapshot, station_overview, zone_summary
from dashboard.geo import geocode, nearest_stations
from dashboard.ui import AQI, aqi_label, category_of, swatch


def _parquet(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_parquet(buf)
    return buf.getvalue()


class DictSource:
    def __init__(self, objects):
        self.objects = objects

    def get(self, key):
        if key not in self.objects:
            raise KeyError(key)
        return self.objects[key]


T = pd.Timestamp("2026-09-24 16:30")


@pytest.fixture
def source():
    stations = pd.DataFrame(
        {
            "location_id": [235, 17],
            "station_name": ["Anand Vihar", "R K Puram"],
            "zone_id": ["delhi_east", "delhi_south"],
            "latitude": [28.647, 28.563],
            "longitude": [77.316, 77.187],
            "agency": ["DPCC", "DPCC"],
            "provider": ["CPCB", "CPCB"],
            "pct_valid_hours": [90.0, 80.0],
        }
    )
    status = pd.DataFrame(
        {
            "location_id": [235, 17],
            "status": ["inactive", "inactive"],
            "last_pm25_reading_utc": [T, T],
            "last_pm25_value": [62.0, 40.0],
            "last_checked_utc": [T, T],
        }
    )
    hourly = pd.DataFrame(
        {
            "location_id": [235, 235, 17],
            "hour_start_utc": [T - pd.Timedelta(hours=1), T, T],
            "pm25": [60.0, 62.0, 40.0],
            "pm25_24h": [55.0, 58.0, None],
            "aqi_category": ["satisfactory", "satisfactory", None],
        }
    )
    zones = pd.DataFrame(
        {"zone_id": ["delhi_east", "delhi_south"], "zone_name": ["East Delhi", "South Delhi"]}
    )
    zh = pd.DataFrame(
        {
            "zone_id": ["delhi_east"],
            "hour_start_utc": [T],
            "pm25_24h_median": [58.0],
            "stations_with_24h_avg": [1],
            "stations_expected": [1],
        }
    )
    objs = {
        "p/st.parquet": _parquet(stations),
        "p/ss.parquet": _parquet(status),
        "p/h.parquet": _parquet(hourly),
        "p/z.parquet": _parquet(zones),
        "p/zh.parquet": _parquet(zh),
        "public/model_card.json": json.dumps({"version": "v1"}).encode(),
    }
    manifest = {
        "exported_at": "2026-09-29T17:00:00Z",
        "datasets": {
            "dim_station": {"keys": ["p/st.parquet"]},
            "station_status": {"keys": ["p/ss.parquet"]},
            "aqi_hourly_recent": {"keys": ["p/h.parquet"]},
            "dim_zone": {"keys": ["p/z.parquet"]},
            "zone_hourly_recent": {"keys": ["p/zh.parquet"]},
            "forecast_accuracy_recent": {"keys": [], "empty": True},
        },
        "files": {"model_card": "public/model_card.json", "drift_summary": "public/missing.json"},
    }
    objs[MANIFEST_KEY] = json.dumps(manifest).encode()
    return DictSource(objs)


def test_load_snapshot_handles_empty_and_missing(source):
    snap = load_snapshot(source)
    assert snap.table("forecast_accuracy_recent").empty
    assert snap.table("not_in_manifest").empty
    assert snap.files["model_card"] == {"version": "v1"}
    assert snap.files["drift_summary"] is None  # missing optional file doesn't break the UI
    assert str(snap.table("aqi_hourly_recent")["hour_start_utc"].dt.tz) == "UTC"


def test_station_overview_joins_latest_24h(source):
    ov = station_overview(load_snapshot(source)).set_index("location_id")
    assert ov.loc[235, "pm25_24h"] == 58.0  # latest non-null 24h value
    assert ov.loc[235, "zone_name"] == "East Delhi"
    assert pd.isna(ov.loc[17, "pm25_24h"])
    assert ov.loc[17, "last_pm25_value"] == 40.0


def test_zone_summary(source):
    z = zone_summary(load_snapshot(source))
    assert z["zone_name"].tolist() == ["East Delhi"]


def test_nearest_stations_sorted_with_distance(source):
    ov = station_overview(load_snapshot(source))
    near = nearest_stations(ov, 28.64, 77.30, n=2)
    assert near["station_name"].tolist() == ["Anand Vihar", "R K Puram"]
    assert near["distance_km"].iloc[0] < 3


def test_geocode_sends_policy_headers_and_parses(monkeypatch):
    import dashboard.geo as geo

    monkeypatch.setattr(geo.time, "sleep", lambda s: None)
    seen = {}

    def handler(request):
        seen["ua"] = request.headers["User-Agent"]
        seen["params"] = dict(request.url.params)
        return httpx.Response(
            200, json=[{"display_name": "Dwarka, Delhi", "lat": "28.59", "lon": "77.05"}]
        )

    place = geocode("Dwarka", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert place.latitude == pytest.approx(28.59)
    assert "CheckYourAQI" in seen["ua"]
    assert seen["params"]["bounded"] == "1" and seen["params"]["countrycodes"] == "in"
    empty = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[])))
    assert geocode("nowhere", client=empty) is None
    assert geocode("   ") is None


@pytest.mark.parametrize(
    ("v", "cat"),
    [
        (12, "good"),
        (30.4, "good"),
        (61, "moderately_polluted"),
        (250, "very_poor"),
        (251, "severe"),
        (None, None),
    ],
)
def test_category_of(v, cat):
    assert category_of(v) == cat


def test_every_swatch_carries_its_label_as_text():
    for key, (label, _) in AQI.items():
        assert label in swatch(key)
    assert aqi_label(None) in swatch(None)

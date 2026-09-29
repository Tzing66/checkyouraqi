import pandas as pd
import pytest
from fastapi.testclient import TestClient

from api.app import app, get_snapshot
from dashboard.data import Snapshot

# Anchored to "now" so freshness assertions don't depend on the calendar: 5 days old = inactive.
T = pd.Timestamp.now(tz="UTC").floor("h") - pd.Timedelta(days=5) + pd.Timedelta(minutes=30)


@pytest.fixture
def client():
    stations = pd.DataFrame(
        {
            "location_id": [235, 17],
            "station_name": ["Anand Vihar", "R K Puram"],
            "zone_id": ["delhi_east", "delhi_south"],
            "latitude": [28.647, 28.563],
            "longitude": [77.316, 77.187],
            "agency": ["DPCC", "DPCC"],
            "provider": ["CPCB", "CPCB"],
        }
    )
    status = pd.DataFrame(
        {
            "location_id": [235, 17],
            "status": ["inactive"] * 2,
            "last_pm25_reading_utc": [T, T],
            "last_pm25_value": [62.0, 40.0],
            "last_checked_utc": [T, T],
        }
    )
    hourly = pd.DataFrame(
        {
            "location_id": [235],
            "hour_start_utc": [T],
            "pm25": [62.0],
            "pm25_24h": [58.0],
            "aqi_category": ["satisfactory"],
        }
    )
    fc = pd.DataFrame(
        {
            "location_id": [235, 235],
            "horizon_h": [48, 24],
            "issue_time_utc": [T + pd.Timedelta(days=5)] * 2,
            "target_hour_start_utc": [T + pd.Timedelta(days=7), T + pd.Timedelta(days=6)],
            "pm25_pred": [95.0, 45.0],
            "is_stale_input": [True, True],
            "input_age_hours": [120, 120],
            "last_valid_hour_utc": [T, T],
            "model_version": ["v1", "v1"],
        }
    )
    snap = Snapshot(
        pd.Timestamp.now(tz="UTC"),
        {
            "dim_station": stations,
            "station_status": status,
            "aqi_hourly_recent": hourly,
            "forecasts_latest": fc,
            "dim_zone": pd.DataFrame({"zone_id": ["delhi_east"], "zone_name": ["East Delhi"]}),
        },
        {"model_card": {"version": "v1"}},
    )
    app.dependency_overrides[get_snapshot] = lambda: snap
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_health_reports_outage(client):
    h = client.get("/health").json()
    assert h["feed_status"] == "outage"
    assert h["status"] == "degraded"
    assert h["stations_total"] == 2 and h["stations_live"] == 0
    assert "paused" in h["feed_message"]
    assert h["model_version"] == "v1"


def test_stations_carry_freshness_and_filter_by_zone(client):
    all_ = client.get("/stations").json()
    assert {s["location_id"] for s in all_} == {235, 17}
    s = next(s for s in all_ if s["location_id"] == 235)
    assert s["freshness"]["status"] == "inactive"  # 5 days old: inactive, not yet offline
    assert s["freshness"]["last_reading_ist"].endswith(":00 IST")  # :30 UTC = :00 IST
    assert s["freshness"]["message"]
    assert s["pm25_24h"] == 58.0 and s["zone_name"] == "East Delhi"
    assert [
        x["location_id"] for x in client.get("/stations", params={"zone": "delhi_south"}).json()
    ] == [17]


def test_forecast_sorted_labelled_and_warned(client):
    r = client.get("/forecast/235").json()
    assert [f["horizon_h"] for f in r["forecasts"]] == [24, 48]
    assert r["forecasts"][0]["aqi_category_indicative"] == "satisfactory"
    assert r["forecasts"][1]["aqi_category_indicative"] == "poor"
    assert all(f["is_stale_input"] for f in r["forecasts"])
    assert "last known readings" in r["warning"]
    assert r["model_version"] == "v1"


def test_forecast_for_station_without_forecast_and_unknown(client):
    r = client.get("/forecast/17").json()
    assert r["forecasts"] == [] and r["warning"] is None
    assert client.get("/forecast/999").status_code == 404


def test_nearest_validates_bounds(client):
    near = client.get("/stations/nearest", params={"lat": 28.64, "lon": 77.30, "n": 1}).json()
    assert near[0]["location_id"] == 235 and near[0]["distance_km"] < 3
    assert client.get("/stations/nearest", params={"lat": 40, "lon": 77}).status_code == 422

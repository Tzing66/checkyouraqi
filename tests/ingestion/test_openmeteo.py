import gzip
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from ingestion.http import ApiError
from ingestion.openmeteo import client as om
from ingestion.openmeteo.client import OpenMeteoClient
from ingestion.openmeteo.extract import (
    StaleForecastRunError,
    extract_actuals,
    extract_air_quality,
    extract_air_quality_forecast,
    extract_forecast,
    extract_previous_runs,
)
from ingestion.openmeteo.models import HourlySeries, WeatherPoint
from ingestion.openmeteo.points import zone_points
from ingestion.rate_limit import RateLimiter
from ingestion.writers import LocalWriter

EAST = WeatherPoint("delhi_east", 28.6468, 77.3160)
GURGAON = WeatherPoint("gurugram", 28.4501, 77.0263)


def make_client(handler, clock):
    calls = []

    def recording(request):
        calls.append(request)
        return handler(request)

    http = httpx.Client(transport=httpx.MockTransport(recording))
    limiter = RateLimiter(0.2, clock=clock, sleep=clock.sleep)
    return OpenMeteoClient(http=http, limiter=limiter, sleep=clock.sleep), calls


def respond(payload, status=200):
    return lambda request: httpx.Response(status, json=payload)


# --- models ---------------------------------------------------------------------------


def test_series_rows_are_utc(load_fixture):
    series = HourlySeries.model_validate(load_fixture("openmeteo/archive.json"))
    rows = series.rows()
    assert len(rows) == 48
    assert rows[0]["time_utc"] == datetime(2024, 11, 1, tzinfo=UTC)
    assert set(series.variables) == set(om.VARIABLES)
    assert rows[0]["boundary_layer_height"] is not None


def test_series_rejects_non_utc_and_ragged_arrays(load_fixture):
    raw = load_fixture("openmeteo/archive.json")
    with pytest.raises(ValueError, match="UTC"):
        HourlySeries.model_validate({**raw, "utc_offset_seconds": 19800})
    ragged = {**raw, "hourly": {**raw["hourly"], "precipitation": [0.0]}}
    with pytest.raises(ValueError, match="precipitation"):
        HourlySeries.model_validate(ragged)


# --- client ---------------------------------------------------------------------------


def test_forecast_multi_point_request(load_fixture, clock):
    c, calls = make_client(respond(load_fixture("openmeteo/forecast.json")), clock)
    fetched = c.forecast([EAST, GURGAON])
    params = calls[0].url.params
    assert str(calls[0].url).startswith(om.FORECAST_URL)
    assert params["latitude"] == "28.6468,28.4501"
    assert params["longitude"] == "77.3160,77.0263"
    assert params["timezone"] == "GMT"
    assert params["wind_speed_unit"] == "ms"
    assert params["hourly"].split(",") == list(om.VARIABLES)
    assert params["forecast_days"] == "4"
    assert len(fetched.items) == 2
    assert len(fetched.items[0].rows()) == 96


def test_single_point_object_response_is_normalised(load_fixture, clock):
    c, _ = make_client(respond(load_fixture("openmeteo/archive.json")), clock)
    fetched = c.archive([EAST], date(2024, 11, 1), date(2024, 11, 2))
    assert len(fetched.items) == 1


def test_point_count_mismatch_is_an_error(load_fixture, clock):
    c, _ = make_client(respond(load_fixture("openmeteo/archive.json")), clock)
    with pytest.raises(ValueError, match="asked for 2 points, got 1"):
        c.archive([EAST, GURGAON], date(2024, 11, 1), date(2024, 11, 1))


def test_previous_runs_asks_for_each_day_and_skips_blh(load_fixture, clock):
    c, calls = make_client(respond(load_fixture("openmeteo/previous_runs.json")), clock)
    c.previous_runs([EAST], date(2024, 11, 1), date(2024, 11, 1))
    hourly = calls[0].url.params["hourly"].split(",")
    assert "temperature_2m_previous_day1" in hourly
    assert "wind_speed_10m_previous_day3" in hourly
    assert not any(v.startswith("boundary_layer_height") for v in hourly)
    assert len(hourly) == len(om.PREVIOUS_RUN_VARIABLES) * 3


def test_bad_request_surfaces_reason(load_fixture, clock):
    c, calls = make_client(respond(load_fixture("openmeteo/error_400.json"), 400), clock)
    with pytest.raises(ApiError, match="not_a_variable"):
        c.forecast([EAST])
    assert len(calls) == 1


def test_rejects_empty_points_and_reversed_dates(clock):
    c, _ = make_client(respond({}), clock)
    with pytest.raises(ValueError):
        c.forecast([])
    with pytest.raises(ValueError):
        c.archive([EAST], date(2024, 11, 2), date(2024, 11, 1))


# --- points ---------------------------------------------------------------------------


def test_zone_points_one_per_zone_skipping_colocated(tmp_path):
    (tmp_path / "stations.yaml").write_text(
        "stations:\n"
        "- {location_id: 1, latitude: 28.0, longitude: 77.0, colocated_with: null}\n"
        "- {location_id: 2, latitude: 28.2, longitude: 77.2, colocated_with: null}\n"
        "- {location_id: 3, latitude: 50.0, longitude: 50.0, colocated_with: 1}\n"
        "- {location_id: 4, latitude: 28.5, longitude: 77.5, colocated_with: null}\n"
    )
    (tmp_path / "zones.yaml").write_text(
        "zones:\n- {id: a, stations: [1, 2, 3]}\n- {id: b, stations: [4]}\n"
    )
    assert zone_points(tmp_path) == [
        WeatherPoint("a", 28.1, 77.1),
        WeatherPoint("b", 28.5, 77.5),
    ]


def test_real_config_gives_nine_zone_points():
    points = zone_points()
    assert len(points) == 9
    assert all(28.3 < p.latitude < 28.9 and 76.8 < p.longitude < 77.6 for p in points)


# --- extract --------------------------------------------------------------------------

NOW = datetime(2026, 9, 28, 14, 5, tzinfo=UTC)
HOUR = datetime(2026, 9, 28, 14, tzinfo=UTC)


def test_extract_forecast_records_issue_time(load_fixture, clock, tmp_path):
    c, _ = make_client(respond(load_fixture("openmeteo/forecast.json")), clock)
    uri = extract_forecast(c, LocalWriter(tmp_path), [EAST, GURGAON], logical_hour=HOUR, now=NOW)
    assert uri.endswith("bronze/openmeteo/forecasts/dt=2026-09-28/hour=14/forecast.json.gz")
    doc = json.loads(gzip.decompress(Path(uri).read_bytes()))
    assert doc["forecast_issued_at"] == "2026-09-28T14:05:00Z"
    assert doc["points"][0] == {"id": "delhi_east", "latitude": 28.6468, "longitude": 77.316}
    assert len(doc["responses"][0]) == 2


def test_extract_forecast_refuses_backfilled_hours(clock, tmp_path):
    c, calls = make_client(respond({}), clock)
    with pytest.raises(StaleForecastRunError):
        extract_forecast(
            c, LocalWriter(tmp_path), [EAST], logical_hour=HOUR - timedelta(days=1), now=NOW
        )
    assert calls == []


def test_extract_actuals_and_previous_runs_keys(load_fixture, clock, tmp_path):
    w = LocalWriter(tmp_path)
    c, calls = make_client(respond(load_fixture("openmeteo/archive.json")), clock)
    uri = extract_actuals(c, w, [EAST], date(2024, 11, 1), date(2024, 11, 2))
    assert uri.endswith(
        "bronze/openmeteo/actuals/dt=2024-11-01/actuals_2024-11-01_2024-11-02.json.gz"
    )
    assert calls[0].url.params["end_date"] == "2024-11-02"
    doc = json.loads(gzip.decompress(Path(uri).read_bytes()))
    assert doc["start_date"] == "2024-11-01"
    assert "fetched_at" in doc
    c, _ = make_client(respond(load_fixture("openmeteo/previous_runs.json")), clock)
    assert extract_previous_runs(c, w, [EAST], date(2024, 11, 1), date(2024, 11, 1)).endswith(
        "previous_runs/dt=2024-11-01/previous_runs_2024-11-01_2024-11-01.json.gz"
    )


def test_air_quality_forecast_and_history(load_fixture, clock, tmp_path):
    c, calls = make_client(respond(load_fixture("openmeteo/air_quality_forecast.json")), clock)
    uri = extract_air_quality_forecast(
        c, LocalWriter(tmp_path), [EAST, GURGAON], logical_hour=HOUR, now=NOW
    )
    assert str(calls[0].url).startswith(om.AIR_QUALITY_URL)
    assert calls[0].url.params["hourly"] == "pm2_5,pm10"
    assert uri.endswith("air_quality_forecasts/dt=2026-09-28/hour=14/air_quality_forecast.json.gz")
    assert json.loads(gzip.decompress(Path(uri).read_bytes()))["forecast_issued_at"]

    c, calls = make_client(respond(load_fixture("openmeteo/air_quality_history.json")), clock)
    day = date(2024, 11, 1)
    uri = extract_air_quality(c, LocalWriter(tmp_path), [EAST], day, day)
    assert calls[0].url.params["start_date"] == "2024-11-01"
    assert uri.endswith("air_quality/dt=2024-11-01/air_quality_2024-11-01_2024-11-01.json.gz")


def test_air_quality_forecast_refuses_backfilled_hours(clock, tmp_path):
    c, calls = make_client(respond({}), clock)
    with pytest.raises(StaleForecastRunError):
        extract_air_quality_forecast(
            c, LocalWriter(tmp_path), [EAST], logical_hour=HOUR - timedelta(hours=5), now=NOW
        )
    assert calls == []

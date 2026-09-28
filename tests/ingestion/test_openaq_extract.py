import gzip
import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from ingestion.http import ApiAuthError, ApiServerError, Fetched
from ingestion.openaq.extract import (
    AllStationsFailedError,
    StationRef,
    extract_history_month,
    extract_hourly,
    history_key,
    load_stations,
)
from ingestion.writers import LocalWriter

HOUR = datetime(2026, 9, 28, 14, 0, tzinfo=UTC)
STATIONS = [StationRef(235, 12235610), StationRef(50, 12234796)]


class FakeClient:
    def __init__(self, fail=(), exc=ApiServerError):
        self.fail = set(fail)
        self.exc = exc
        self.hours_calls = []

    def sensor_hours(self, sensor_id, start, end):
        self.hours_calls.append((sensor_id, start, end))
        if sensor_id in self.fail:
            raise self.exc(f"boom {sensor_id}")
        return Fetched([], [{"sensor": sensor_id}])

    def latest(self, location_id):
        if location_id in self.fail:
            raise self.exc(f"boom {location_id}")
        return Fetched([], [{"location": location_id}])


def read(uri):
    return json.loads(gzip.decompress(Path(uri).read_bytes()))


def test_hourly_uses_trailing_window_and_writes_both_files(tmp_path):
    client = FakeClient()
    now = datetime(2026, 9, 28, 14, 7, tzinfo=UTC)
    result = extract_hourly(
        client, LocalWriter(tmp_path), STATIONS, logical_hour=HOUR.replace(minute=5), now=now
    )
    assert result == {"stations": 2, "pm25_errors": 0, "latest_errors": 0}
    assert client.hours_calls[0] == (12235610, datetime(2026, 9, 28, 8, tzinfo=UTC), HOUR)

    base = tmp_path / "bronze/openaq"
    pm = read(base / "measurements/dt=2026-09-28/hour=14/pm25_hours.json.gz")
    assert pm["window_start"] == "2026-09-28T08:00:00Z"
    assert pm["fetched_at"] == "2026-09-28T14:07:00Z"
    assert pm["stations"]["235"] == [{"sensor": 12235610}]
    latest = read(base / "latest/dt=2026-09-28/hour=14/latest.json.gz")
    assert latest["stations"]["50"] == [{"location": 50}]


def test_one_failing_station_is_recorded_not_fatal(tmp_path):
    client = FakeClient(fail={12235610})
    result = extract_hourly(client, LocalWriter(tmp_path), STATIONS, logical_hour=HOUR)
    assert result["pm25_errors"] == 1
    pm = read(tmp_path / "bronze/openaq/measurements/dt=2026-09-28/hour=14/pm25_hours.json.gz")
    assert "235" in pm["errors"]
    assert "50" in pm["stations"]


def test_all_stations_failing_fails_the_run(tmp_path):
    client = FakeClient(fail={12235610, 12234796})
    with pytest.raises(AllStationsFailedError):
        extract_hourly(client, LocalWriter(tmp_path), STATIONS, logical_hour=HOUR)


def test_auth_error_fails_fast(tmp_path):
    client = FakeClient(fail={12235610, 12234796}, exc=ApiAuthError)
    with pytest.raises(ApiAuthError):
        extract_hourly(client, LocalWriter(tmp_path), STATIONS, logical_hour=HOUR)
    assert len(client.hours_calls) == 1


@pytest.mark.parametrize(
    ("month", "start", "end"),
    [
        (date(2024, 11, 1), datetime(2024, 11, 1, tzinfo=UTC), datetime(2024, 12, 1, tzinfo=UTC)),
        (date(2024, 12, 1), datetime(2024, 12, 1, tzinfo=UTC), datetime(2025, 1, 1, tzinfo=UTC)),
    ],
)
def test_history_month_window(tmp_path, month, start, end):
    client = FakeClient()
    extract_history_month(client, LocalWriter(tmp_path), STATIONS[0], month)
    assert client.hours_calls == [(12235610, start, end)]
    doc = read(tmp_path / history_key(month, 235))
    assert doc["sensor_id"] == 12235610


def test_load_stations_from_real_config():
    stations = load_stations()
    assert len(stations) == 70
    assert StationRef(235, 12235610) in stations

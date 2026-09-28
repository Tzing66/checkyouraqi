from datetime import UTC, datetime

from ingestion.openaq.models import (
    PM25_PARAMETER_ID,
    LatestReading,
    Location,
    Measurement,
    Meta,
)


def test_location_parses_recorded_response(load_fixture):
    loc = Location.model_validate(load_fixture("openaq/location_235.json")["results"][0])
    assert loc.id == 235
    assert loc.is_monitor is True
    assert loc.provider.name == "CPCB"
    assert loc.timezone == "Asia/Kolkata"
    assert loc.datetime_last.utc == datetime(2026, 9, 24, 17, 30, tzinfo=UTC)


def test_location_lists_legacy_and_live_pm25_sensors(load_fixture):
    loc = Location.model_validate(load_fixture("openaq/location_235.json")["results"][0])
    assert {s.id for s in loc.sensors_for(PM25_PARAMETER_ID)} == {384, 12235610}


def test_latest_includes_dead_sensors(load_fixture):
    rows = [
        LatestReading.model_validate(r)
        for r in load_fixture("openaq/location_235_latest.json")["results"]
    ]
    by_sensor = {r.sensors_id: r for r in rows}
    assert by_sensor[384].datetime.utc.year == 2018  # legacy sensor, long dead
    assert by_sensor[12235610].datetime.utc == datetime(2026, 9, 24, 17, 30, tzinfo=UTC)


def test_hours_are_ist_aligned(load_fixture):
    hours = [
        Measurement.model_validate(r)
        for r in load_fixture("openaq/sensor_12235610_hours.json")["results"]
    ]
    first = hours[0]
    assert first.period.label == "1hour"
    assert first.parameter.id == PM25_PARAMETER_ID
    assert first.period.datetime_from.utc.minute == 30
    assert first.coverage.percent_complete == 100.0


def test_raw_measurements_are_15_minute(load_fixture):
    m = Measurement.model_validate(
        load_fixture("openaq/sensor_12235610_measurements.json")["results"][0]
    )
    assert m.period.label == "raw"
    assert m.period.interval == "00:15:00"


def test_meta_found_accepts_int_numeric_string_and_lower_bound():
    assert Meta.model_validate({"page": 1, "limit": 3, "found": 7}).found == 7
    assert Meta.model_validate({"page": 1, "limit": 3, "found": "7"}).found == 7
    assert Meta.model_validate({"page": 1, "limit": 3, "found": ">3"}).found == ">3"


def test_unknown_fields_are_ignored(load_fixture):
    raw = load_fixture("openaq/location_235.json")["results"][0]
    raw["someNewField"] = {"added": "by openaq"}
    assert Location.model_validate(raw).id == 235

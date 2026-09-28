from datetime import UTC, datetime

import pytest

from ingestion.openaq.models import LatestReading, Location
from ingestion.openaq.stations import (
    Station,
    agency_from_name,
    assign_zone,
    build_station,
    flag_colocated,
    is_candidate,
    is_reference_grade,
    select_stations,
)

NOW = datetime(2026, 9, 28, tzinfo=UTC)


def loc(
    id=1,
    name="Somewhere, Delhi - DPCC",
    is_monitor=True,
    provider="CPCB",
    last="2026-09-24T17:30:00Z",
    sensors=((10, 2, "pm25", "µg/m³"),),
    lat=28.6,
    lon=77.2,
):
    return Location.model_validate(
        {
            "id": id,
            "name": name,
            "timezone": "Asia/Kolkata",
            "isMonitor": is_monitor,
            "provider": {"id": 1, "name": provider} if provider else None,
            "coordinates": {"latitude": lat, "longitude": lon},
            "datetimeLast": {"utc": last} if last else None,
            "sensors": [
                {"id": sid, "name": n, "parameter": {"id": pid, "name": n, "units": u}}
                for sid, pid, n, u in sensors
            ],
        }
    )


def reading(sensor_id, utc, location_id=1):
    return LatestReading.model_validate(
        {"datetime": {"utc": utc}, "value": 1.0, "sensorsId": sensor_id, "locationsId": location_id}
    )


@pytest.mark.parametrize(
    ("name", "agency"),
    [
        ("Anand Vihar, New Delhi - DPCC", "DPCC"),
        ("Sector - 62, Noida, UP - IMD", "IMD"),
        ("IHBAS, Dilshad Garden,New Delhi - CPCB", "CPCB"),
        ("New Delhi", None),
        ("Air Check", None),
    ],
)
def test_agency_from_name(name, agency):
    assert agency_from_name(name) == agency


def test_reference_grade_rules():
    assert is_reference_grade(loc())
    # Newer OpenAQ entries: isMonitor=false, provider N/A, but a government name.
    assert is_reference_grade(loc(name="JNU, Delhi - DPCC", is_monitor=False, provider="N/A"))
    assert not is_reference_grade(loc(name="Air Check", is_monitor=False, provider="AirGradient"))
    # Low-cost provider is excluded even if flagged or named like a monitor.
    assert not is_reference_grade(loc(name="X - DPCC", is_monitor=True, provider="AirGradient"))
    assert not is_reference_grade(loc(name="Random sensor", is_monitor=False, provider="N/A"))


def test_candidate_requires_recent_pm25():
    assert is_candidate(loc(), NOW)
    assert not is_candidate(loc(last="2022-10-31T00:30:00Z"), NOW)  # legacy duplicate location
    assert not is_candidate(loc(last=None), NOW)
    assert not is_candidate(loc(sensors=((11, 1, "pm10", "µg/m³"),)), NOW)


def test_build_station_picks_live_pm25_over_legacy(load_fixture):
    location = Location.model_validate(load_fixture("openaq/location_235.json")["results"][0])
    latest = [
        LatestReading.model_validate(r)
        for r in load_fixture("openaq/location_235_latest.json")["results"]
    ]
    station = build_station(location, latest)
    assert station.pm25_sensor_id == 12235610  # not 384, which died in 2018
    assert 384 not in station.sensors.values()
    assert station.agency == "DPCC"
    # CO is reported in two units but only the ppb sensor is live, so it keeps the plain name.
    assert station.sensors["co"] == 12235605


def test_build_station_disambiguates_units_when_both_live():
    location = loc(
        sensors=((10, 2, "pm25", "µg/m³"), (20, 4, "co", "µg/m³"), (21, 102, "co", "ppb"))
    )
    latest = [reading(i, "2026-09-24T17:30:00Z") for i in (10, 20, 21)]
    assert build_station(location, latest).sensors == {"co_ppb": 21, "co_ugm3": 20, "pm25": 10}


def test_build_station_none_without_live_pm25():
    location = loc(sensors=((10, 2, "pm25", "µg/m³"), (11, 1, "pm10", "µg/m³")))
    latest = [reading(10, "2019-01-01T00:00:00Z"), reading(11, "2026-09-24T17:30:00Z")]
    assert build_station(location, latest) is None


@pytest.mark.parametrize(
    ("name", "lat", "lon", "zone"),
    [
        ("Knowledge Park - III, Greater Noida - UPPCB", 28.47, 77.48, "noida"),
        ("Sector-2 IMT, Manesar - HSPCB", 28.36, 76.94, "gurugram"),
        ("Uptitude Cloud - Sector 62, GGN", 28.40, 77.08, "gurugram"),
        ("Prashant Garden, Khora - UPPCB", 28.60, 77.35, "ghaziabad"),
        ("Sector 11, Faridabad - HSPCB", 28.38, 77.32, "faridabad"),
        ("Mandir Marg, New Delhi - DPCC", 28.636429, 77.201067, "delhi_central"),
        ("Anand Vihar, New Delhi - DPCC", 28.646835, 77.316032, "delhi_east"),
        ("Punjabi Bagh, Delhi - DPCC", 28.674045, 77.131023, "delhi_west"),
        ("Narela, Delhi - DPCC", 28.822836, 77.101981, "delhi_north"),
        ("Okhla Phase-2, Delhi - DPCC", 28.530785, 77.271255, "delhi_south"),
    ],
)
def test_assign_zone(name, lat, lon, zone):
    assert assign_zone(name, lat, lon)[0] == zone


def _station(id, lat, lon):
    return Station(id, f"s{id}", "DPCC", "CPCB", lat, lon, "Asia/Kolkata", pm25_sensor_id=id)


def test_flag_colocated_marks_higher_id_only():
    stations = [
        _station(6356, 28.639645, 77.146262),  # Pusa DPCC
        _station(5404, 28.639645, 77.146263),  # Pusa IMD, same spot
        _station(17, 28.563262, 77.186937),  # R K Puram, far away
    ]
    flag_colocated(stations)
    by_id = {s.location_id: s for s in stations}
    assert by_id[6356].colocated_with == 5404
    assert by_id[5404].colocated_with is None
    assert by_id[17].colocated_with is None


def test_select_stations_end_to_end(load_fixture):
    locations = [
        Location.model_validate(r) for r in load_fixture("openaq/locations_bbox.json")["results"]
    ]
    latest = {
        235: [
            LatestReading.model_validate(r)
            for r in load_fixture("openaq/location_235_latest.json")["results"]
        ]
    }
    stations = select_stations(locations, latest, NOW)
    # 5509 is a legacy Anand Vihar location last seen in 2022, so only 235 survives.
    assert [s.location_id for s in stations] == [235]
    assert stations[0].zone == "delhi_east"

from datetime import UTC, datetime, timedelta

import pytest

from ingestion.freshness import (
    FeedStatus,
    StationStatus,
    Thresholds,
    classify,
    feed_message,
    feed_report,
    format_ist,
    station_message,
)

NOW = datetime(2026, 9, 28, 14, 30, tzinfo=UTC)
T = Thresholds.load()
H = timedelta(hours=1)


@pytest.mark.parametrize(
    ("age", "status"),
    [
        (timedelta(0), StationStatus.LIVE),
        (3 * H, StationStatus.LIVE),
        (3 * H + timedelta(minutes=1), StationStatus.DELAYED),
        (6 * H, StationStatus.DELAYED),
        (7 * H, StationStatus.INACTIVE),
        (timedelta(days=7), StationStatus.INACTIVE),
        (timedelta(days=8), StationStatus.OFFLINE),
    ],
)
def test_classify_by_age(age, status):
    assert classify(NOW - age, NOW, T) == status


def test_classify_no_data():
    assert classify(None, NOW, T) == StationStatus.NO_DATA


def test_feed_outage_when_almost_everyone_is_quiet():
    readings = {i: NOW - timedelta(days=3, hours=21) for i in range(9)} | {99: NOW - H}
    report = feed_report(readings, NOW, T)
    assert report.status == FeedStatus.OUTAGE
    assert report.share_not_live == pytest.approx(0.9)
    assert report.newest_reading_utc == NOW - H


def test_feed_degraded_and_ok():
    stale = NOW - timedelta(days=2)
    assert feed_report({1: stale, 2: NOW, 3: NOW}, NOW, T).status == FeedStatus.DEGRADED
    assert feed_report({1: stale, 2: NOW, 3: NOW, 4: NOW, 5: NOW}, NOW, T).status == FeedStatus.OK


def test_feed_with_no_stations_is_outage():
    assert feed_report({}, NOW, T).status == FeedStatus.OUTAGE


def test_format_ist_converts_from_utc():
    assert format_ist(datetime(2026, 9, 24, 17, 30, tzinfo=UTC)) == "24 Sep, 23:00 IST"


def test_live_station_has_no_caveat():
    ok = feed_report({1: NOW}, NOW, T)
    assert station_message(StationStatus.LIVE, NOW, NOW, ok) is None
    assert feed_message(ok) is None


def test_single_inactive_sensor_blames_the_sensor():
    last = NOW - timedelta(days=2)
    ok = feed_report({1: last, 2: NOW, 3: NOW, 4: NOW, 5: NOW}, NOW, T)
    msg = station_message(classify(last, NOW, T), last, NOW, ok)
    assert "sensor appears inactive" in msg
    assert "26 Sep, 20:00 IST (2 days ago)" in msg


def test_outage_blames_the_service_not_the_sensor():
    last = datetime(2026, 9, 24, 17, 30, tzinfo=UTC)
    outage = feed_report({1: last, 2: last}, NOW, T)
    msg = station_message(classify(last, NOW, T), last, NOW, outage)
    assert "data service isn't sending new readings" in msg
    assert "24 Sep, 23:00 IST" in msg
    assert "since 24 Sep, 23:00 IST" in feed_message(outage)


def test_offline_station_says_excluded_from_averages():
    last = NOW - timedelta(days=30)
    ok = feed_report({1: last, 2: NOW, 3: NOW, 4: NOW, 5: NOW}, NOW, T)
    assert "excluded from area averages" in station_message(StationStatus.OFFLINE, last, NOW, ok)

"""Entry points the Airflow DAGs call. Each takes the run's logical time and is idempotent.

Kept free of Airflow imports so they can be run and tested without it:
    PYTHONPATH=. uv run python -c "from ingestion import jobs; jobs.fires_daily(...)"
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

from ingestion.cpcb.client import CpcbClient
from ingestion.cpcb.extract import capture_snapshot
from ingestion.firms.client import FirmsClient
from ingestion.firms.extract import extract_fires
from ingestion.openaq.client import OpenAQClient
from ingestion.openaq.discover import load_city
from ingestion.openaq.extract import extract_hourly, load_stations
from ingestion.openmeteo.client import OpenMeteoClient
from ingestion.openmeteo.extract import (
    extract_actuals,
    extract_air_quality,
    extract_air_quality_forecast,
    extract_forecast,
    extract_previous_runs,
)
from ingestion.openmeteo.points import zone_points
from ingestion.settings import Settings
from ingestion.writers import S3Writer

log = logging.getLogger(__name__)

ACTUALS_LOOKBACK_DAYS = 7  # ERA5 lags ~5 days, so keep re-fetching the last week
FIRES_DAYS_BACK = (1, 2)  # NRT for a day keeps filling in for a while after it ends
# Live previous-runs window: the (h/24 + 1)-days-old forecasts for target hours up to 72h ahead.
# One file per day (same key all day), so the latest fetch of the day wins.
PREVIOUS_RUNS_LIVE_DAYS = (-1, 4)


def _writer(settings: Settings) -> S3Writer:
    return S3Writer.from_profile(settings.data_bucket, settings.aws_profile, settings.aws_region)


def _hour(dt: datetime) -> datetime:
    return dt.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def openaq_hourly(logical_time: datetime) -> dict[str, int]:
    s = Settings()
    with OpenAQClient(s.openaq_api_key) as client:
        result = extract_hourly(
            client, _writer(s), load_stations(), logical_hour=_hour(logical_time)
        )
    log.info("openaq hourly %s: %s", _hour(logical_time), result)
    return result


def cpcb_snapshot_hourly(logical_time: datetime) -> dict:
    """Backup source: CPCB's live feed keeps no history, so capture it every hour."""
    s = Settings()
    with CpcbClient() as client:
        result = capture_snapshot(client, _writer(s), logical_hour=_hour(logical_time))
    log.info(
        "cpcb snapshot %s: %d stations, updated %s",
        _hour(logical_time),
        result["stations"],
        result["last_updates"],
    )
    return result


def weather_forecast_hourly(logical_time: datetime) -> list[str]:
    """Weather + CAMS forecasts (stored with their issue time), and the previous-runs forecasts
    for the next days' target hours, which live prediction uses exactly as training did."""
    s = Settings()
    writer, points, hour = _writer(s), zone_points(), _hour(logical_time)
    today = hour.date()
    first, last = (today + timedelta(days=d) for d in PREVIOUS_RUNS_LIVE_DAYS)
    with OpenMeteoClient() as client:
        return [
            extract_forecast(client, writer, points, logical_hour=hour),
            extract_air_quality_forecast(client, writer, points, logical_hour=hour),
            extract_previous_runs(client, writer, points, first, last),
        ]


def weather_actuals_daily(run_day: date) -> list[str]:
    """Observed weather (ERA5) + CAMS history for the last week (both lag a few days)."""
    s = Settings()
    writer, points = _writer(s), zone_points()
    start = run_day - timedelta(days=ACTUALS_LOOKBACK_DAYS)
    end = run_day - timedelta(days=1)
    with OpenMeteoClient() as client:
        return [
            extract_actuals(client, writer, points, start, end),
            extract_air_quality(client, writer, points, start, end),
        ]


def fires_daily(run_day: date) -> dict[str, dict[str, int]]:
    s = Settings()
    bbox = load_city()["fire_bbox"]
    writer = _writer(s)
    out = {}
    with FirmsClient(s.firms_map_key) as client:
        availability = client.availability()
        for back in FIRES_DAYS_BACK:
            day = run_day - timedelta(days=back)
            out[day.isoformat()] = extract_fires(client, writer, day, bbox, availability)
    log.info("fires %s: %s", run_day, out)
    return out

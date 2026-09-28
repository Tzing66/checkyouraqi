"""Entry points the Airflow DAGs call. Each takes the run's logical time and is idempotent.

Kept free of Airflow imports so they can be run and tested without it:
    PYTHONPATH=. uv run python -c "from ingestion import jobs; jobs.fires_daily(...)"
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

from ingestion.firms.client import FirmsClient
from ingestion.firms.extract import extract_fires
from ingestion.openaq.client import OpenAQClient
from ingestion.openaq.discover import load_city
from ingestion.openaq.extract import extract_hourly, load_stations
from ingestion.openmeteo.client import OpenMeteoClient
from ingestion.openmeteo.extract import extract_actuals, extract_forecast
from ingestion.openmeteo.points import zone_points
from ingestion.settings import Settings
from ingestion.writers import S3Writer

log = logging.getLogger(__name__)

ACTUALS_LOOKBACK_DAYS = 7  # ERA5 lags ~5 days, so keep re-fetching the last week
FIRES_DAYS_BACK = (1, 2)  # NRT for a day keeps filling in for a while after it ends


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


def weather_forecast_hourly(logical_time: datetime) -> str:
    s = Settings()
    with OpenMeteoClient() as client:
        return extract_forecast(client, _writer(s), zone_points(), logical_hour=_hour(logical_time))


def weather_actuals_daily(run_day: date) -> str:
    s = Settings()
    start = run_day - timedelta(days=ACTUALS_LOOKBACK_DAYS)
    end = run_day - timedelta(days=1)
    with OpenMeteoClient() as client:
        return extract_actuals(client, _writer(s), zone_points(), start, end)


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

"""Land Open-Meteo data in bronze. Each function is idempotent for its partition.

bronze/openmeteo/forecasts/dt=YYYY-MM-DD/hour=HH/forecast.json      (hourly, live only)
bronze/openmeteo/actuals/dt=YYYY-MM-DD/actuals.json                 (daily, re-fetched)
bronze/openmeteo/previous_runs/dt=YYYY-MM-DD/previous_runs.json     (backfill)
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from typing import Any

from ingestion.http import Fetched
from ingestion.openmeteo.client import OpenMeteoClient
from ingestion.openmeteo.models import HourlySeries, WeatherPoint
from ingestion.writers import Writer

# The forecast API only ever returns *today's* forecast, so a run for an old logical hour
# would store today's forecast under a past issue time: exactly the leakage we must avoid.
MAX_FORECAST_LAG = timedelta(hours=2)


class StaleForecastRunError(RuntimeError):
    pass


def _envelope(
    points: list[WeatherPoint], fetched: Fetched[HourlySeries], **meta: Any
) -> dict[str, Any]:
    return {
        **meta,
        "points": [asdict(p) for p in points],
        "responses": fetched.raw_pages,
    }


def extract_forecast(
    client: OpenMeteoClient,
    writer: Writer,
    points: list[WeatherPoint],
    *,
    logical_hour: datetime,
    now: datetime | None = None,
) -> str:
    now = now or datetime.now(UTC)
    if abs(now - logical_hour) > MAX_FORECAST_LAG:
        raise StaleForecastRunError(
            f"refusing to label a forecast fetched at {now:%Y-%m-%dT%H:%MZ} as issued for "
            f"{logical_hour:%Y-%m-%dT%H:%MZ}; use previous_runs for history"
        )
    fetched = client.forecast(points)
    key = (
        f"bronze/openmeteo/forecasts/dt={logical_hour:%Y-%m-%d}"
        f"/hour={logical_hour:%H}/forecast.json"
    )
    return writer.put_json(
        key,
        _envelope(
            points,
            fetched,
            forecast_issued_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            logical_hour=logical_hour.strftime("%Y-%m-%dT%H:%M:%SZ"),
        ),
    )


def extract_actuals(
    client: OpenMeteoClient, writer: Writer, points: list[WeatherPoint], day: date
) -> str:
    fetched = client.archive(points, day, day)
    key = f"bronze/openmeteo/actuals/dt={day:%Y-%m-%d}/actuals.json"
    return writer.put_json(key, _envelope(points, fetched, date=day.isoformat()))


def extract_previous_runs(
    client: OpenMeteoClient, writer: Writer, points: list[WeatherPoint], day: date
) -> str:
    fetched = client.previous_runs(points, day, day)
    key = f"bronze/openmeteo/previous_runs/dt={day:%Y-%m-%d}/previous_runs.json"
    return writer.put_json(key, _envelope(points, fetched, date=day.isoformat()))

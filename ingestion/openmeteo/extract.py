"""Land Open-Meteo data in bronze. Each function is idempotent for its key.

bronze/openmeteo/forecasts/dt=YYYY-MM-DD/hour=HH/forecast.json.gz          (hourly, live only)
bronze/openmeteo/actuals/dt=<start>/actuals_<start>_<end>.json.gz          (date range)
bronze/openmeteo/previous_runs/dt=<start>/previous_runs_<start>_<end>.json.gz

Ranges overlap across runs (the daily actuals job re-fetches the last week because ERA5 lags
~5 days), so silver dedupes on (point, hour) keeping the most recent `fetched_at`.
`dt` is the first date a file covers. Backfills use month-long ranges: Open-Meteo weighs
calls by points x span, so one call per month stays far inside the free daily budget.
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


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _envelope(
    points: list[WeatherPoint], fetched: Fetched[HourlySeries], **meta: Any
) -> dict[str, Any]:
    return {
        "fetched_at": _iso(datetime.now(UTC)),
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
        f"/hour={logical_hour:%H}/forecast.json.gz"
    )
    return writer.put_json(
        key,
        _envelope(points, fetched, forecast_issued_at=_iso(now), logical_hour=_iso(logical_hour)),
    )


def range_key(kind: str, start: date, end: date) -> str:
    span = f"{start:%Y-%m-%d}_{end:%Y-%m-%d}"
    return f"bronze/openmeteo/{kind}/dt={start:%Y-%m-%d}/{kind}_{span}.json.gz"


def extract_actuals(
    client: OpenMeteoClient, writer: Writer, points: list[WeatherPoint], start: date, end: date
) -> str:
    fetched = client.archive(points, start, end)
    return writer.put_json(
        range_key("actuals", start, end),
        _envelope(points, fetched, start_date=start.isoformat(), end_date=end.isoformat()),
    )


def extract_previous_runs(
    client: OpenMeteoClient, writer: Writer, points: list[WeatherPoint], start: date, end: date
) -> str:
    fetched = client.previous_runs(points, start, end)
    return writer.put_json(
        range_key("previous_runs", start, end),
        _envelope(points, fetched, start_date=start.isoformat(), end_date=end.isoformat()),
    )

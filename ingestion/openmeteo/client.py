"""Open-Meteo client: forecasts, historical actuals (ERA5 archive) and previous model runs.

No API key. Free tier is 600 calls/min and 10k/day, where every location in a multi-point
request counts as a call, so we ask for a handful of zone points, not every station.

Leakage (plan §7): training must use the forecast that existed at prediction time.
- Live: `forecast()` is stored with the time we fetched it (`forecast_issued_at`).
- Backfill: `previous_runs()` returns "the forecast for hour H as issued N days earlier",
  which gives leak-free weather features for the 24/48/72h horizons over past years.
  Boundary layer height has no previous-run data, so it is only used as an observed value.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Sequence
from datetime import date
from typing import Any

import httpx

from ingestion.http import Fetched, HttpApi
from ingestion.openmeteo.models import HourlySeries, WeatherPoint
from ingestion.rate_limit import RateLimiter

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
PREVIOUS_RUNS_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
AIR_QUALITY_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"

VARIABLES = (
    "temperature_2m",
    "relative_humidity_2m",
    "wind_speed_10m",
    "wind_direction_10m",
    "precipitation",
    "boundary_layer_height",
    "surface_pressure",
)
# Verified 2026-09-28: previous runs exist for all of these back to 2024-09, but not for BLH.
PREVIOUS_RUN_VARIABLES = tuple(v for v in VARIABLES if v != "boundary_layer_height")
PREVIOUS_RUN_DAYS = (1, 2, 3)
MAX_POINTS_PER_REQUEST = 50

# CAMS (via Open-Meteo air quality), history from ~2022-09. Verified 2026-09-28: there are
# NO previous-run values, and past hours are the model's best estimate of that hour. So the
# history is only leak-free as a lagged feature; the CAMS *forecast* baseline must come from
# forecasts we collect live with their issue time.
AIR_QUALITY_VARIABLES = ("pm2_5", "pm10")

COMMON_PARAMS = {"timezone": "GMT", "timeformat": "iso8601", "wind_speed_unit": "ms"}


class OpenMeteoClient:
    def __init__(
        self,
        *,
        http: httpx.Client | None = None,
        limiter: RateLimiter | None = None,
        max_retries: int = 5,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._api = HttpApi(
            "Open-Meteo",
            http or httpx.Client(timeout=60),
            limiter or RateLimiter(0.2, sleep=sleep),
            max_retries=max_retries,
            sleep=sleep,
        )

    def __enter__(self) -> OpenMeteoClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self._api.close()

    def forecast(
        self, points: Sequence[WeatherPoint], *, forecast_days: int = 4, past_days: int = 1
    ) -> Fetched[HourlySeries]:
        """Current forecast. 4 days covers the 72h horizon from any hour of today."""
        return self._fetch(
            FORECAST_URL,
            points,
            {"hourly": VARIABLES, "forecast_days": forecast_days, "past_days": past_days},
        )

    def archive(
        self, points: Sequence[WeatherPoint], start: date, end: date
    ) -> Fetched[HourlySeries]:
        """Observed (reanalysis) weather. ERA5 lags ~5 days; later days come back as nulls."""
        return self._fetch(ARCHIVE_URL, points, {"hourly": VARIABLES, **_dates(start, end)})

    def air_quality_forecast(
        self, points: Sequence[WeatherPoint], *, forecast_days: int = 4, past_days: int = 1
    ) -> Fetched[HourlySeries]:
        """Current CAMS PM2.5/PM10 forecast (the plan's CAMS baseline)."""
        return self._fetch(
            AIR_QUALITY_URL,
            points,
            {
                "hourly": AIR_QUALITY_VARIABLES,
                "forecast_days": forecast_days,
                "past_days": past_days,
            },
        )

    def air_quality(
        self, points: Sequence[WeatherPoint], start: date, end: date
    ) -> Fetched[HourlySeries]:
        """CAMS PM2.5/PM10 for past dates (best estimate per hour, not archived forecasts)."""
        return self._fetch(
            AIR_QUALITY_URL, points, {"hourly": AIR_QUALITY_VARIABLES, **_dates(start, end)}
        )

    def previous_runs(
        self,
        points: Sequence[WeatherPoint],
        start: date,
        end: date,
        days: Iterable[int] = PREVIOUS_RUN_DAYS,
    ) -> Fetched[HourlySeries]:
        """For each hour, the forecast as issued `d` days earlier (`<var>_previous_day<d>`)."""
        hourly = [f"{v}_previous_day{d}" for d in days for v in PREVIOUS_RUN_VARIABLES]
        return self._fetch(PREVIOUS_RUNS_URL, points, {"hourly": hourly, **_dates(start, end)})

    def _fetch(
        self, url: str, points: Sequence[WeatherPoint], params: dict[str, Any]
    ) -> Fetched[HourlySeries]:
        if not points:
            raise ValueError("no weather points given")
        if len(points) > MAX_POINTS_PER_REQUEST:
            raise ValueError(f"at most {MAX_POINTS_PER_REQUEST} points per request")
        query = {
            **COMMON_PARAMS,
            **{k: ",".join(v) if isinstance(v, list | tuple) else v for k, v in params.items()},
            "latitude": ",".join(f"{p.latitude:.4f}" for p in points),
            "longitude": ",".join(f"{p.longitude:.4f}" for p in points),
        }
        payload = self._api.get_json(url, query)
        # One point comes back as an object, several as a list in request order.
        results = payload if isinstance(payload, list) else [payload]
        if len(results) != len(points):
            raise ValueError(f"asked for {len(points)} points, got {len(results)}")
        return Fetched([HourlySeries.model_validate(r) for r in results], [payload])


def _dates(start: date, end: date) -> dict[str, str]:
    if end < start:
        raise ValueError("end date is before start date")
    return {"start_date": start.isoformat(), "end_date": end.isoformat()}

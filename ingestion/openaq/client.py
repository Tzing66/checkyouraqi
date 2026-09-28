"""OpenAQ v3 client with proactive throttling, 429 back-off and retries.

Every call returns both the parsed models and the raw page payloads, so extractors can
validate with pydantic and still land the untouched JSON in bronze.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel

from ingestion.http import (
    ApiAuthError,
    ApiError,
    ApiRateLimitError,
    ApiServerError,
    Fetched,
    HttpApi,
)
from ingestion.openaq.models import LatestReading, Location, Measurement, Meta
from ingestion.rate_limit import RateLimiter

BASE_URL = "https://api.openaq.org/v3"
# OpenAQ allows ~60 requests/minute; stay a little under it.
DEFAULT_MIN_INTERVAL_S = 1.1
PAGE_LIMIT = 1000

T = TypeVar("T", bound=BaseModel)

# Source-specific names for the shared transport errors.
OpenAQError = ApiError
OpenAQAuthError = ApiAuthError
OpenAQRateLimitError = ApiRateLimitError
OpenAQServerError = ApiServerError


class OpenAQClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = BASE_URL,
        http: httpx.Client | None = None,
        limiter: RateLimiter | None = None,
        max_retries: int = 5,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise OpenAQAuthError("OpenAQ API key is empty")
        http = http or httpx.Client(base_url=base_url, timeout=30)
        http.headers["X-API-Key"] = api_key
        self._api = HttpApi(
            "OpenAQ",
            http,
            limiter or RateLimiter(DEFAULT_MIN_INTERVAL_S, sleep=sleep),
            max_retries=max_retries,
            sleep=sleep,
        )

    def __enter__(self) -> OpenAQClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._api.close()

    # --- endpoints -----------------------------------------------------------------------

    def locations(self, bbox: Sequence[float]) -> Fetched[Location]:
        """All locations in a [min_lon, min_lat, max_lon, max_lat] box."""
        if len(bbox) != 4:
            raise ValueError("bbox must be [min_lon, min_lat, max_lon, max_lat]")
        return self._paginate("/locations", {"bbox": ",".join(map(str, bbox))}, Location)

    def location(self, location_id: int) -> Fetched[Location]:
        return self._paginate(f"/locations/{location_id}", {}, Location)

    def latest(self, location_id: int) -> Fetched[LatestReading]:
        """Latest reading for every sensor at a location (including dead legacy sensors)."""
        return self._paginate(f"/locations/{location_id}/latest", {}, LatestReading)

    def sensor_hours(self, sensor_id: int, start: datetime, end: datetime) -> Fetched[Measurement]:
        """Hourly aggregates in [start, end). Hours are IST-aligned, so UTC starts at :30."""
        return self._paginate(f"/sensors/{sensor_id}/hours", _window(start, end), Measurement)

    def sensor_measurements(
        self, sensor_id: int, start: datetime, end: datetime
    ) -> Fetched[Measurement]:
        """Raw readings (15-minute for CPCB) in [start, end)."""
        return self._paginate(
            f"/sensors/{sensor_id}/measurements", _window(start, end), Measurement
        )

    # --- transport -----------------------------------------------------------------------

    def _paginate(self, path: str, params: dict[str, Any], model: type[T]) -> Fetched[T]:
        out: Fetched[T] = Fetched()
        page = 1
        while True:
            payload = self._api.get_json(path, {**params, "limit": PAGE_LIMIT, "page": page})
            Meta.model_validate(payload["meta"])
            results = payload["results"]
            out.raw_pages.append(payload)
            out.items.extend(model.model_validate(r) for r in results)
            # meta.found is unreliable (can be ">1000"), so stop on a short page instead.
            if len(results) < PAGE_LIMIT:
                return out
            page += 1


def _window(start: datetime, end: datetime) -> dict[str, str]:
    for dt in (start, end):
        if dt.tzinfo is None or dt.utcoffset() != timedelta(0):
            raise ValueError("start/end must be timezone-aware UTC datetimes")
    if end <= start:
        raise ValueError("end must be after start")
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return {"datetime_from": start.strftime(fmt), "datetime_to": end.strftime(fmt)}

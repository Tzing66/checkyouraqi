"""OpenAQ v3 client with proactive throttling, 429 back-off and retries.

Every call returns both the parsed models and the raw page payloads, so extractors can
validate with pydantic and still land the untouched JSON in bronze.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Generic, TypeVar

import httpx
from pydantic import BaseModel

from ingestion.openaq.models import LatestReading, Location, Measurement, Meta
from ingestion.rate_limit import RateLimiter

log = logging.getLogger(__name__)

BASE_URL = "https://api.openaq.org/v3"
# OpenAQ allows ~60 requests/minute; stay a little under it.
DEFAULT_MIN_INTERVAL_S = 1.1
PAGE_LIMIT = 1000

T = TypeVar("T", bound=BaseModel)


class OpenAQError(Exception):
    pass


class OpenAQAuthError(OpenAQError):
    """Bad or missing API key. Never retried: hammering with a bad key risks a ban."""


class OpenAQRateLimitError(OpenAQError):
    pass


class OpenAQServerError(OpenAQError):
    pass


@dataclass
class Fetched(Generic[T]):
    items: list[T] = field(default_factory=list)
    raw_pages: list[dict[str, Any]] = field(default_factory=list)


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
        self._http = http or httpx.Client(base_url=base_url, timeout=30)
        self._http.headers["X-API-Key"] = api_key
        self._limiter = limiter or RateLimiter(DEFAULT_MIN_INTERVAL_S, sleep=sleep)
        self._max_retries = max_retries
        self._sleep = sleep

    def __enter__(self) -> OpenAQClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

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
            payload = self._get(path, {**params, "limit": PAGE_LIMIT, "page": page})
            Meta.model_validate(payload["meta"])
            results = payload["results"]
            out.raw_pages.append(payload)
            out.items.extend(model.model_validate(r) for r in results)
            # meta.found is unreliable (can be ">1000"), so stop on a short page instead.
            if len(results) < PAGE_LIMIT:
                return out
            page += 1

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        last_error = ""
        for attempt in range(self._max_retries):
            self._limiter.wait()
            try:
                resp = self._http.get(path, params=params)
            except httpx.TransportError as e:
                last_error = f"transport error: {e}"
                self._sleep(_backoff(attempt))
                continue

            if resp.status_code in (401, 403):
                raise OpenAQAuthError(f"{path}: HTTP {resp.status_code} {resp.text[:200]}")
            if resp.status_code == 429:
                wait = max(_int(resp.headers.get("x-ratelimit-reset")) or 0, _backoff(attempt))
                log.warning("OpenAQ 429 on %s, sleeping %.0fs", path, wait)
                last_error = "HTTP 429"
                self._sleep(wait)
                continue
            if resp.status_code >= 500:
                last_error = f"HTTP {resp.status_code}"
                self._sleep(_backoff(attempt))
                continue
            if resp.status_code >= 400:
                raise OpenAQError(f"{path}: HTTP {resp.status_code} {resp.text[:200]}")

            self._limiter.observe(resp.headers)
            return resp.json()

        if last_error == "HTTP 429":
            raise OpenAQRateLimitError(
                f"{path}: still rate limited after {self._max_retries} tries"
            )
        raise OpenAQServerError(f"{path}: {last_error} after {self._max_retries} tries")


def _window(start: datetime, end: datetime) -> dict[str, str]:
    for dt in (start, end):
        if dt.tzinfo is None or dt.utcoffset() != timedelta(0):
            raise ValueError("start/end must be timezone-aware UTC datetimes")
    if end <= start:
        raise ValueError("end must be after start")
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return {"datetime_from": start.strftime(fmt), "datetime_to": end.strftime(fmt)}


def _backoff(attempt: int) -> float:
    return float(min(2**attempt * 2, 60))


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None

"""Shared JSON-over-HTTP transport: proactive throttling, 429 back-off, retries.

Each source client (OpenAQ, Open-Meteo, FIRMS) wraps one of these and only deals with its
own endpoints and response models.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

import httpx

from ingestion.rate_limit import RateLimiter

log = logging.getLogger(__name__)

# httpx logs every request URL at INFO. FIRMS puts its key in the URL path, so keep
# request logging off to avoid leaking keys into Airflow task logs.
logging.getLogger("httpx").setLevel(logging.WARNING)


T = TypeVar("T")


@dataclass
class Fetched(Generic[T]):
    """Parsed items plus the untouched response payloads (what bronze stores)."""

    items: list[T] = field(default_factory=list)
    raw_pages: list[Any] = field(default_factory=list)


class ApiError(Exception):
    pass


class ApiAuthError(ApiError):
    """Bad or missing key. Never retried: hammering with a bad key risks a ban."""


class ApiRateLimitError(ApiError):
    pass


class ApiServerError(ApiError):
    pass


class HttpApi:
    def __init__(
        self,
        name: str,
        http: httpx.Client,
        limiter: RateLimiter,
        *,
        max_retries: int = 5,
        sleep: Callable[[float], None] = time.sleep,
        redact: str | None = None,
    ) -> None:
        self.name = name
        self._http = http
        self._limiter = limiter
        self._max_retries = max_retries
        self._sleep = sleep
        self._redact = redact

    def close(self) -> None:
        self._http.close()

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        # A 200 with a truncated or empty body happens occasionally (seen on Open-Meteo's
        # archive); treat it like a 5xx and retry.
        for attempt in range(self._max_retries):
            resp = self.get(url, params)
            try:
                return resp.json()
            except ValueError:
                log.warning("%s %s: 200 with non-JSON body, retrying", self.name, self._safe(url))
                self._sleep(backoff(attempt))
        raise ApiServerError(
            f"{self.name} {self._safe(url)}: non-JSON body after {self._max_retries} tries"
        )

    def get(self, url: str, params: dict[str, Any] | None = None) -> httpx.Response:
        label = self._safe(url)
        last_error = ""
        for attempt in range(self._max_retries):
            self._limiter.wait()
            try:
                resp = self._http.get(url, params=params)
            except httpx.TransportError as e:
                last_error = f"transport error: {type(e).__name__}"
                self._sleep(backoff(attempt))
                continue

            if resp.status_code in (401, 403):
                raise ApiAuthError(
                    f"{self.name} {label}: HTTP {resp.status_code} {self._body(resp)}"
                )
            if resp.status_code == 429:
                wait = max(_int(resp.headers.get("x-ratelimit-reset")) or 0, backoff(attempt))
                log.warning("%s 429 on %s, sleeping %.0fs", self.name, label, wait)
                last_error = "HTTP 429"
                self._sleep(wait)
                continue
            if resp.status_code >= 500:
                last_error = f"HTTP {resp.status_code}"
                self._sleep(backoff(attempt))
                continue
            if resp.status_code >= 400:
                raise ApiError(f"{self.name} {label}: HTTP {resp.status_code} {self._body(resp)}")

            self._limiter.observe(resp.headers)
            return resp

        if last_error == "HTTP 429":
            raise ApiRateLimitError(
                f"{self.name} {label}: still rate limited after {self._max_retries} tries"
            )
        raise ApiServerError(f"{self.name} {label}: {last_error} after {self._max_retries} tries")

    def _safe(self, text: str) -> str:
        return text.replace(self._redact, "***") if self._redact else text

    def _body(self, resp: httpx.Response) -> str:
        """The API's own error message if it sent one (OpenAQ `detail`, Open-Meteo `reason`)."""
        text = resp.text
        try:
            body = resp.json()
            if isinstance(body, dict):
                text = next(
                    (str(body[k]) for k in ("reason", "detail", "message") if k in body), text
                )
        except ValueError:
            pass
        return self._safe(text[:500])


def backoff(attempt: int) -> float:
    return float(min(2**attempt * 2, 60))


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None

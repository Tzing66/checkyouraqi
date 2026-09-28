"""NASA FIRMS active-fire client (VIIRS), for upwind stubble-burning features.

The map key goes in the URL path, so it is redacted from every error message and httpx
request logging is off (see ingestion/http.py). Limit: 5000 transactions per 10 minutes.

FIRMS serves each satellite as two products: near-real-time (`_NRT`, the last ~3 months)
and standard processing (`_SP`, the archive). Which one covers a date moves over time, so
we pick per date from the live availability table.
"""

from __future__ import annotations

import csv
import io
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date

import httpx

from ingestion.firms.models import FirePoint
from ingestion.http import ApiAuthError, ApiError, HttpApi
from ingestion.rate_limit import RateLimiter

BASE_URL = "https://firms.modaps.eosdis.nasa.gov"
# VIIRS on Suomi-NPP and NOAA-20 both have NRT + SP back past our 2-year backfill.
# (NOAA-21 has no SP archive and MODIS is much coarser, so they're left out for consistency.)
SENSORS = ("VIIRS_SNPP", "VIIRS_NOAA20")
CSV_HEADER_PREFIX = "latitude,longitude,"


@dataclass(frozen=True)
class Availability:
    min_date: date
    max_date: date

    def covers(self, day: date) -> bool:
        return self.min_date <= day <= self.max_date


@dataclass(frozen=True)
class FireCsv:
    source: str
    day: date
    text: str
    points: list[FirePoint]


class FirmsClient:
    def __init__(
        self,
        map_key: str,
        *,
        http: httpx.Client | None = None,
        limiter: RateLimiter | None = None,
        max_retries: int = 5,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not map_key:
            raise ApiAuthError("FIRMS map key is empty")
        self._key = map_key
        self._api = HttpApi(
            "FIRMS",
            http or httpx.Client(base_url=BASE_URL, timeout=60),
            limiter or RateLimiter(1.0, sleep=sleep),
            max_retries=max_retries,
            sleep=sleep,
            redact=map_key,
        )

    def __enter__(self) -> FirmsClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self._api.close()

    def availability(self) -> dict[str, Availability]:
        text = self._api.get(f"/api/data_availability/csv/{self._key}/all").text
        if not text.startswith("data_id,"):
            raise ApiError(f"FIRMS availability: unexpected response {self._safe(text)[:200]}")
        return {
            row["data_id"]: Availability(
                date.fromisoformat(row["min_date"]), date.fromisoformat(row["max_date"])
            )
            for row in csv.DictReader(io.StringIO(text))
        }

    def fires(self, source: str, bbox: Sequence[float], day: date) -> FireCsv:
        """All detections for one UTC day in bbox [west, south, east, north]."""
        if len(bbox) != 4:
            raise ValueError("bbox must be [west, south, east, north]")
        area = ",".join(str(x) for x in bbox)
        path = f"/api/area/csv/{self._key}/{source}/{area}/1/{day.isoformat()}"
        text = self._api.get(path).text
        # FIRMS reports some errors (e.g. an invalid key) as 200 + plain text, not CSV.
        if not text.startswith(CSV_HEADER_PREFIX):
            raise ApiError(f"FIRMS {source} {day}: not CSV: {self._safe(text)[:200]}")
        points = [FirePoint.model_validate(r) for r in csv.DictReader(io.StringIO(text))]
        return FireCsv(source, day, text, points)

    def _safe(self, text: str) -> str:
        return text.replace(self._key, "***")


def source_for(sensor: str, day: date, availability: dict[str, Availability]) -> str | None:
    """Prefer the archive (SP) product; fall back to NRT; None if neither covers the day."""
    for suffix in ("_SP", "_NRT"):
        source = sensor + suffix
        if source in availability and availability[source].covers(day):
            return source
    return None

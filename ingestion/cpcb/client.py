"""CPCB's public CAAQMS feed: a snapshot of every Indian monitoring station "right now".

Captured hourly as a BACKUP source for OpenAQ, which stalled twice in late Sep 2026
(docs/decisions.md). The feed keeps no history, so an hour not captured is lost for good.
It is an undocumented public endpoint: we store the raw XML untouched and decide how to use
it later. Open questions, to verify against OpenAQ for overlapping hours before any use:
  - Min/Max/Avg per pollutant look like AQI index points, not µg/m³ (e.g. Anand Vihar
    "PM2.5 Avg 97" equalled its AQI value 97), over an unknown (probably 24h) window;
  - stations are identified by name only (match to OpenAQ by coordinates).
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from ingestion.http import ApiError, HttpApi
from ingestion.rate_limit import RateLimiter

FEED_URL = "https://airquality.cpcb.gov.in/caaqms/rss_feed"


@dataclass(frozen=True)
class FeedSnapshot:
    xml: bytes
    stations: int
    last_updates: tuple[str, ...]  # distinct "DD-MM-YYYY HH:MM:SS" (IST) values in the feed


def parse_summary(xml: bytes) -> FeedSnapshot:
    """Validate the payload is the CAAQMS feed and summarise it (raises ApiError if not)."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        raise ApiError(f"CPCB feed is not valid XML: {e}") from e
    if root.tag != "AqIndex":
        raise ApiError(f"CPCB feed: unexpected root element <{root.tag}>")
    stations = root.findall(".//Station")
    if not stations:
        raise ApiError("CPCB feed contains no stations")
    updates = tuple(sorted({s.get("lastupdate", "") for s in stations} - {""}))
    return FeedSnapshot(xml, len(stations), updates)


class CpcbClient:
    def __init__(
        self,
        *,
        http: httpx.Client | None = None,
        max_retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._api = HttpApi(
            "CPCB",
            http or httpx.Client(timeout=60, headers={"User-Agent": "CheckYourAQI/0.1"}),
            RateLimiter(1.0, sleep=sleep),
            max_retries=max_retries,
            sleep=sleep,
        )

    def __enter__(self) -> CpcbClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self._api.close()

    def snapshot(self) -> FeedSnapshot:
        return parse_summary(self._api.get(FEED_URL).content)

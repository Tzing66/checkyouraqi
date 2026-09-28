"""Phase 0 data spike: which OpenAQ stations in Delhi NCR are usable?

For every OpenAQ location in the Delhi NCR bbox, report whether it has a PM2.5 sensor,
when it last reported, and the % of hours with PM2.5 data over the last 30 days.
Stations with PM2.5 and >= 70% coverage are kept. Writes docs/station_coverage.md.

Throwaway script. The real OpenAQ client is built in Phase 1.

    PYTHONPATH=. uv run python scripts/station_coverage_spike.py
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import yaml
from dotenv import load_dotenv

BASE_URL = "https://api.openaq.org/v3"
PM25_PARAMETER_ID = 2
WINDOW_DAYS = 30
KEEP_THRESHOLD = 0.70
MIN_INTERVAL_S = 1.1  # stay under ~60 requests/minute

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "spike"
CACHE_FILE = RAW_DIR / "hours_cache.json"
OUT_MD = ROOT / "docs" / "station_coverage.md"


class ServerError(RuntimeError):
    """OpenAQ kept returning 5xx for this request."""


class Client:
    def __init__(self, api_key: str) -> None:
        self._http = httpx.Client(base_url=BASE_URL, headers={"X-API-Key": api_key}, timeout=30)
        self._last_call = 0.0

    def get(self, path: str, params: dict) -> dict:
        status = None
        for attempt in range(5):
            wait = MIN_INTERVAL_S - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()
            resp = self._http.get(path, params=params)
            status = resp.status_code

            if resp.status_code == 429:
                reset = int(resp.headers.get("x-ratelimit-reset", 0) or 0)
                time.sleep(max(reset, 2**attempt * 5))
                continue
            if resp.status_code >= 500:
                time.sleep(2**attempt * 2)
                continue
            resp.raise_for_status()

            # Slow down before we hit the limit rather than after.
            remaining = resp.headers.get("x-ratelimit-remaining")
            if remaining is not None and int(remaining) <= 2:
                time.sleep(int(resp.headers.get("x-ratelimit-reset", 60) or 60))
            return resp.json()
        if status is not None and status >= 500:
            raise ServerError(f"{path} returned HTTP {status} on every retry")
        raise RuntimeError(f"Gave up on {path} after repeated 429s")


def load_bbox() -> list[float]:
    cities = yaml.safe_load((ROOT / "config" / "cities.yaml").read_text())["cities"]
    return next(c for c in cities if c["id"] == "delhi_ncr")["bbox"]


def fetch_locations(client: Client, bbox: list[float]) -> list[dict]:
    locations, page = [], 1
    while True:
        data = client.get(
            "/locations",
            {"bbox": ",".join(str(x) for x in bbox), "limit": 1000, "page": page},
        )
        locations.extend(data["results"])
        if len(data["results"]) < 1000:
            return locations
        page += 1


def pm25_hours(client: Client, sensor_id: int, start: datetime, end: datetime) -> int:
    """Count distinct hours with a PM2.5 value in [start, end)."""
    hours, page = set(), 1
    while True:
        data = client.get(
            f"/sensors/{sensor_id}/hours",
            {
                "datetime_from": start.isoformat(),
                "datetime_to": end.isoformat(),
                "limit": 1000,
                "page": page,
            },
        )
        for r in data["results"]:
            if r.get("value") is not None:
                hours.add(r["period"]["datetimeFrom"]["utc"])
        if len(data["results"]) < 1000:
            return len(hours)
        page += 1


def main() -> None:
    load_dotenv(ROOT / ".env")
    api_key = os.environ.get("OPENAQ_API_KEY")
    if not api_key:
        raise SystemExit("OPENAQ_API_KEY is not set (add it to .env)")

    client = Client(api_key)
    # Window ends at today's UTC midnight so reruns on the same day hit the cache.
    end = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=WINDOW_DAYS)
    expected_hours = WINDOW_DAYS * 24

    locations = fetch_locations(client, load_bbox())
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    (RAW_DIR / "locations.json").write_text(json.dumps(locations, indent=2))
    print(f"{len(locations)} locations in bbox")

    cache = json.loads(CACHE_FILE.read_text()) if CACHE_FILE.exists() else {}
    rows = []
    for i, loc in enumerate(locations, 1):
        # Active stations often list a long-dead legacy PM2.5 sensor next to the live one,
        # so check every PM2.5 sensor and keep the one with the most hours.
        pm25_ids = [
            s["id"] for s in loc.get("sensors", []) if s["parameter"]["id"] == PM25_PARAMETER_ID
        ]
        last = (loc.get("datetimeLast") or {}).get("utc")
        pm25, coverage, api_error = (pm25_ids[0] if pm25_ids else None), None, False
        if pm25_ids and last and last >= start.isoformat():
            best = -1
            for sensor_id in pm25_ids:
                key = f"{sensor_id}|{start:%Y-%m-%d}|{end:%Y-%m-%d}"
                if key not in cache:
                    try:
                        cache[key] = pm25_hours(client, sensor_id, start, end)
                    except ServerError as e:
                        print(f"  skipped: {e}")
                        cache[key] = None
                    CACHE_FILE.write_text(json.dumps(cache))
                if cache[key] is not None and cache[key] > best:
                    pm25, best = sensor_id, cache[key]
            if best < 0:
                api_error = True
            else:
                coverage = best / expected_hours
        rows.append(
            {
                "location_id": loc["id"],
                "name": loc.get("name"),
                "locality": loc.get("locality"),
                "provider": (loc.get("provider") or {}).get("name"),
                "is_monitor": loc.get("isMonitor"),
                "lat": loc["coordinates"]["latitude"],
                "lon": loc["coordinates"]["longitude"],
                "pm25_sensor_id": pm25,
                "last_utc": last,
                "coverage": coverage,
                "api_error": api_error,
                "keep": bool(pm25 and coverage is not None and coverage >= KEEP_THRESHOLD),
            }
        )
        cov = "API error" if api_error else f"{coverage:.0%}" if coverage is not None else "-"
        print(f"[{i}/{len(locations)}] {loc.get('name')}: pm25={bool(pm25)} coverage={cov}")

    (RAW_DIR / "coverage.json").write_text(json.dumps(rows, indent=2))
    write_markdown(rows, start, end)
    print(f"Kept {sum(r['keep'] for r in rows)} of {len(rows)}. Wrote {OUT_MD.relative_to(ROOT)}")


def write_markdown(rows: list[dict], start: datetime, end: datetime) -> None:
    rows = sorted(rows, key=lambda r: (not r["keep"], -(r["coverage"] or 0)))
    kept = sum(r["keep"] for r in rows)
    lines = [
        "# Delhi NCR station coverage (Phase 0 spike)",
        "",
        f"Window: {start:%Y-%m-%d %H:%M} to {end:%Y-%m-%d %H:%M} UTC ({WINDOW_DAYS} days). "
        f"Keep rule: has PM2.5 and >= {KEEP_THRESHOLD:.0%} of hours with data.",
        "",
        f"**{kept} of {len(rows)} locations kept.** "
        "Generated by `scripts/station_coverage_spike.py`.",
        "",
        "| Keep | Location ID | Name | Provider | Monitor | PM2.5 sensor | Last reading (UTC) "
        "| Coverage | Lat, Lon |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        cov = (
            "API error"
            if r["api_error"]
            else f"{r['coverage']:.0%}"
            if r["coverage"] is not None
            else "-"
        )
        lines.append(
            f"| {'✅' if r['keep'] else ''} | {r['location_id']} | {r['name']} | {r['provider']} "
            f"| {r['is_monitor']} | {r['pm25_sensor_id'] or '-'} | {r['last_utc'] or '-'} "
            f"| {cov} | {r['lat']:.4f}, {r['lon']:.4f} |"
        )
    OUT_MD.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()

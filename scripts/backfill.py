"""Backfill history into bronze. Resumable: finished files are skipped on rerun.

OpenAQ PM2.5 exists only from ~2025-02 (see docs/decisions.md), so that's where it starts.
Weather, CAMS and fires start a month earlier so lag features are available from day one.

    PYTHONPATH=. uv run python scripts/backfill.py openaq
    PYTHONPATH=. uv run python scripts/backfill.py weather     # actuals, previous runs, CAMS
    PYTHONPATH=. uv run python scripts/backfill.py fires
    ... add --local to write to data/ instead of S3, --start/--end to narrow the range.
"""

from __future__ import annotations

import argparse
import logging
import time
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import yaml

from ingestion.firms.client import SENSORS, FirmsClient, source_for
from ingestion.firms.extract import extract_fires
from ingestion.http import ApiAuthError, ApiError
from ingestion.openaq.client import OpenAQClient
from ingestion.openaq.discover import load_city
from ingestion.openaq.extract import StationRef, extract_history_month, history_key
from ingestion.openmeteo.client import OpenMeteoClient
from ingestion.openmeteo.extract import (
    extract_actuals,
    extract_air_quality,
    extract_previous_runs,
    range_key,
)
from ingestion.openmeteo.points import zone_points
from ingestion.settings import CONFIG_DIR, ROOT, Settings
from ingestion.writers import LocalWriter, S3Writer, Writer

log = logging.getLogger("backfill")

OPENAQ_START = date(2025, 2, 1)
CONTEXT_START = date(2025, 1, 1)
# Days this recent are always re-fetched: FIRMS NRT and ERA5 keep filling in.
REFRESH_DAYS = 7


def months(start: date, end: date) -> Iterator[tuple[date, date]]:
    """(first_day, last_day) per calendar month, clipped to [start, end]."""
    cur = start.replace(day=1)
    while cur <= end:
        nxt = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
        yield max(cur, start), min(nxt - timedelta(days=1), end)
        cur = nxt


def days(start: date, end: date) -> Iterator[date]:
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def is_recent(last_day: date, end: date) -> bool:
    return (end - last_day).days < REFRESH_DAYS


def backfill_openaq(writer: Writer, s: Settings, start: date, end: date) -> None:
    stations = yaml.safe_load((CONFIG_DIR / "stations.yaml").read_text())["stations"]
    todo = []
    for st in stations:
        first = date.fromisoformat(st["first_seen_utc"][:10])
        for m_start, m_end in months(max(start, OPENAQ_START), end):
            if m_end < first:
                continue
            key = history_key(m_start, st["location_id"])
            if not is_recent(m_end, end) and writer.exists(key):
                continue
            todo.append((StationRef(st["location_id"], st["pm25_sensor_id"]), m_start))
    log.info("openaq: %d station-months to fetch (~%.0f min)", len(todo), len(todo) * 1.2 / 60)
    hours, failed = 0, []
    with OpenAQClient(s.openaq_api_key) as client:
        for i, (station, month) in enumerate(todo, 1):
            try:
                hours += extract_history_month(client, writer, station, month)
            except ApiAuthError:
                raise
            except ApiError as e:
                # e.g. Wave City's sensor: /hours always 500s. Skip it, keep the rest.
                log.warning("openaq: skipped station %s %s: %s", station.location_id, month, e)
                failed.append((station.location_id, month.strftime("%Y-%m")))
            if i % 50 == 0 or i == len(todo):
                log.info("openaq: %d/%d done, %d station-hours so far", i, len(todo), hours)
    if failed:
        log.warning("openaq: %d station-months skipped (rerun to retry): %s", len(failed), failed)


def backfill_weather(writer: Writer, s: Settings, start: date, end: date) -> None:
    points = zone_points()
    jobs = [
        ("actuals", extract_actuals),
        ("previous_runs", extract_previous_runs),
        ("air_quality", extract_air_quality),
    ]
    with OpenMeteoClient() as client:
        for m_start, m_end in months(max(start, CONTEXT_START), end):
            for kind, fn in jobs:
                if not is_recent(m_end, end) and writer.exists(range_key(kind, m_start, m_end)):
                    continue
                fn(client, writer, points, m_start, m_end)
                # Open-Meteo weighs month x 9 points as many calls; pace well under 600/min.
                time.sleep(2)
            log.info("weather: %s done", m_start.strftime("%Y-%m"))


def backfill_fires(writer: Writer, s: Settings, start: date, end: date) -> None:
    bbox = load_city()["fire_bbox"]
    with FirmsClient(s.firms_map_key) as client:
        availability = client.availability()
        todo = []
        for day in days(max(start, CONTEXT_START), end):
            sources = [source_for(sensor, day, availability) for sensor in SENSORS]
            done = all(
                src is None or writer.exists(f"bronze/firms/dt={day:%Y-%m-%d}/{src}.csv")
                for src in sources
            )
            if is_recent(day, end) or not done:
                todo.append(day)
        log.info("fires: %d days to fetch (~%.0f min)", len(todo), len(todo) * 2.1 / 60)
        detections = 0
        for i, day in enumerate(todo, 1):
            detections += sum(extract_fires(client, writer, day, bbox, availability).values())
            if i % 30 == 0 or i == len(todo):
                log.info("fires: %d/%d days done, %d detections so far", i, len(todo), detections)


SOURCES = {"openaq": backfill_openaq, "weather": backfill_weather, "fires": backfill_fires}


def main() -> None:
    yesterday = datetime.now(UTC).date() - timedelta(days=1)
    parser = argparse.ArgumentParser(description="Backfill history into bronze (resumable).")
    parser.add_argument("source", choices=sorted(SOURCES))
    parser.add_argument("--start", type=date.fromisoformat, default=CONTEXT_START)
    parser.add_argument("--end", type=date.fromisoformat, default=yesterday)
    parser.add_argument("--local", action="store_true", help="write to data/ instead of S3")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    s = Settings()
    writer: Writer = (
        LocalWriter(ROOT / "data")
        if args.local
        else S3Writer.from_profile(s.data_bucket, s.aws_profile, s.aws_region)
    )
    started = time.monotonic()
    SOURCES[args.source](writer, s, args.start, args.end)
    log.info("%s backfill finished in %.1f min", args.source, (time.monotonic() - started) / 60)


if __name__ == "__main__":
    main()

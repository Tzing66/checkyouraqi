"""Land OpenAQ readings in bronze.

Hourly (DAG `ingest_openaq`), for every station in config/stations.yaml:
  bronze/openaq/measurements/dt=YYYY-MM-DD/hour=HH/pm25_hours.json.gz
      PM2.5 hourly aggregates for the trailing LOOKBACK window, so readings that arrive late
      are picked up by later runs. Windows overlap on purpose; staging dedupes on
      (sensor_id, hour).
  bronze/openaq/latest/dt=YYYY-MM-DD/hour=HH/latest.json.gz
      Latest value of every sensor (all pollutants): the source for "last reading" and the
      freshness badges, even when a station has been silent for days.

Backfill (scripts/backfill.py), one file per station and month:
  bronze/openaq/measurements_history/month=YYYY-MM/station=<id>.json.gz

One station failing (e.g. an OpenAQ 500 for one sensor) never loses the others: errors are
recorded in the envelope and the run only fails if every station failed.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from ingestion.http import ApiAuthError, ApiError, Fetched
from ingestion.openaq.client import OpenAQClient
from ingestion.settings import CONFIG_DIR
from ingestion.writers import Writer

log = logging.getLogger(__name__)

LOOKBACK = timedelta(hours=6)


@dataclass(frozen=True)
class StationRef:
    location_id: int
    pm25_sensor_id: int


class AllStationsFailedError(RuntimeError):
    pass


def load_stations(config_dir: Path = CONFIG_DIR) -> list[StationRef]:
    doc = yaml.safe_load((config_dir / "stations.yaml").read_text())
    return [StationRef(s["location_id"], s["pm25_sensor_id"]) for s in doc["stations"]]


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _per_station(
    stations: Sequence[StationRef], fetch: Callable[[StationRef], Fetched[Any]]
) -> tuple[dict[str, Any], dict[str, str]]:
    """Run fetch per station; collect raw pages and per-station errors."""
    results: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for st in stations:
        try:
            results[str(st.location_id)] = fetch(st).raw_pages
        except ApiAuthError:
            raise  # a bad key affects every station; fail fast, don't retry 70 times
        except ApiError as e:
            log.warning("station %s: %s", st.location_id, e)
            errors[str(st.location_id)] = str(e)
    if stations and not results:
        raise AllStationsFailedError(f"all {len(stations)} stations failed: {errors}")
    return results, errors


def extract_hourly(
    client: OpenAQClient,
    writer: Writer,
    stations: Sequence[StationRef],
    *,
    logical_hour: datetime,
    now: datetime | None = None,
) -> dict[str, int]:
    """Returns counts for logging: {'stations': n, 'pm25_errors': n, 'latest_errors': n}."""
    hour = logical_hour.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    start = hour - LOOKBACK
    fetched_at = _iso(now or datetime.now(UTC))
    partition = f"dt={hour:%Y-%m-%d}/hour={hour:%H}"
    meta = {"logical_hour": _iso(hour), "fetched_at": fetched_at}

    pm25, pm25_errors = _per_station(
        stations, lambda st: client.sensor_hours(st.pm25_sensor_id, start, hour)
    )
    writer.put_json(
        f"bronze/openaq/measurements/{partition}/pm25_hours.json.gz",
        {
            **meta,
            "window_start": _iso(start),
            "window_end": _iso(hour),
            "stations": pm25,
            "errors": pm25_errors,
        },
    )

    latest, latest_errors = _per_station(stations, lambda st: client.latest(st.location_id))
    writer.put_json(
        f"bronze/openaq/latest/{partition}/latest.json.gz",
        {**meta, "stations": latest, "errors": latest_errors},
    )
    return {
        "stations": len(stations),
        "pm25_errors": len(pm25_errors),
        "latest_errors": len(latest_errors),
    }


def history_key(month: date, location_id: int) -> str:
    return f"bronze/openaq/measurements_history/month={month:%Y-%m}/station={location_id}.json.gz"


def extract_history_month(
    client: OpenAQClient, writer: Writer, station: StationRef, month: date
) -> int:
    """PM2.5 hourly aggregates for one station and calendar month. Returns hours fetched."""
    start = datetime(month.year, month.month, 1, tzinfo=UTC)
    end = datetime(month.year + (month.month == 12), month.month % 12 + 1, 1, tzinfo=UTC)
    fetched = client.sensor_hours(station.pm25_sensor_id, start, end)
    writer.put_json(
        history_key(month, station.location_id),
        {
            "fetched_at": _iso(datetime.now(UTC)),
            "location_id": station.location_id,
            "sensor_id": station.pm25_sensor_id,
            "window_start": _iso(start),
            "window_end": _iso(end),
            "pages": fetched.raw_pages,
        },
    )
    return len(fetched.items)

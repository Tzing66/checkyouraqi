"""CheckYourAQI forecast API (FastAPI). Reads only the public/ snapshots, like the dashboard.

    PYTHONPATH=. uv run uvicorn api.app:app --reload        # http://127.0.0.1:8000/docs

Timestamps are UTC ISO-8601 plus an IST display string. Every station and forecast carries
its freshness, so a client can never show stale data as if it were live (owner requirement).
Phase 5 wraps this with Mangum behind a Lambda Function URL.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from typing import Annotated

import pandas as pd
from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel

from dashboard.data import Snapshot, default_source, load_snapshot, station_overview
from dashboard.geo import nearest_stations
from dashboard.ui import category_of, feed_state, ist, station_banner, station_state, to_utc

CACHE_TTL_S = 300

app = FastAPI(
    title="CheckYourAQI",
    version="0.1.0",
    description=(
        "PM2.5 forecasts (24/48/72h) for Delhi NCR monitoring stations. Data: OpenAQ (CPCB), "
        "Open-Meteo, NASA FIRMS. Every response says how fresh its data is."
    ),
)


class _Cache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snap: Snapshot | None = None
        self._loaded = 0.0

    def get(self) -> Snapshot:
        with self._lock:
            if self._snap is None or time.monotonic() - self._loaded > CACHE_TTL_S:
                self._snap = load_snapshot(default_source())
                self._loaded = time.monotonic()
            return self._snap


_cache = _Cache()


def get_snapshot() -> Snapshot:
    return _cache.get()


Snap = Annotated[Snapshot, Depends(get_snapshot)]


# --- response models ----------------------------------------------------------------------


class Freshness(BaseModel):
    status: str  # live | delayed | inactive | offline | no_data
    last_reading_utc: datetime | None
    last_reading_ist: str
    message: str | None


class Station(BaseModel):
    location_id: int
    name: str
    zone_id: str
    zone_name: str | None
    latitude: float
    longitude: float
    last_pm25: float | None
    pm25_24h: float | None
    aqi_category: str | None
    freshness: Freshness
    distance_km: float | None = None


class Forecast(BaseModel):
    horizon_h: int
    target_hour_start_utc: datetime
    target_hour_ist: str
    pm25_pred: float
    aqi_category_indicative: str | None
    is_stale_input: bool
    input_age_hours: int | None
    inputs_as_of_utc: datetime | None


class StationForecast(BaseModel):
    station: Station
    model_version: str | None
    issued_at_utc: datetime | None
    forecasts: list[Forecast]
    warning: str | None


class Health(BaseModel):
    status: str  # ok | degraded
    exported_at_utc: datetime
    snapshot_age_minutes: float
    feed_status: str  # ok | degraded | outage
    feed_message: str | None
    stations_total: int
    stations_live: int
    model_version: str | None


# --- helpers ------------------------------------------------------------------------------


def _none(v):
    return None if v is None or pd.isna(v) else v


def _feed(snap: Snapshot):
    status = snap.table("station_status")
    last = dict(zip(status["location_id"], status["last_pm25_reading_utc"], strict=True))
    return feed_state(last, datetime.now(UTC))


def _station(row, feed) -> Station:
    last = row.last_pm25_reading_utc
    return Station(
        location_id=int(row.location_id),
        name=row.station_name,
        zone_id=row.zone_id,
        zone_name=_none(getattr(row, "zone_name", None)),
        latitude=float(row.latitude),
        longitude=float(row.longitude),
        last_pm25=_none(row.last_pm25_value),
        pm25_24h=_none(row.pm25_24h),
        aqi_category=_none(row.aqi_category),
        freshness=Freshness(
            status=station_state(last).value,
            last_reading_utc=to_utc(last),
            last_reading_ist=ist(last),
            message=station_banner(last, feed),
        ),
        distance_km=_none(getattr(row, "distance_km", None)),
    )


# --- endpoints ----------------------------------------------------------------------------


@app.get("/health", response_model=Health)
def health(snap: Snap) -> Health:
    feed, message = _feed(snap)
    stations = snap.table("station_status")
    live = sum(station_state(t).value == "live" for t in stations["last_pm25_reading_utc"])
    card = snap.files.get("model_card") or {}
    exported = to_utc(snap.exported_at)
    age = (datetime.now(UTC) - exported).total_seconds() / 60
    return Health(
        status="ok" if feed.status.value == "ok" and age < 180 else "degraded",
        exported_at_utc=exported,
        snapshot_age_minutes=round(age, 1),
        feed_status=feed.status.value,
        feed_message=message,
        stations_total=len(stations),
        stations_live=live,
        model_version=card.get("version"),
    )


@app.get("/stations", response_model=list[Station])
def stations(snap: Snap, zone: str | None = None) -> list[Station]:
    df = station_overview(snap)
    if zone:
        df = df[df["zone_id"] == zone]
    feed, _ = _feed(snap)
    return [_station(r, feed) for r in df.itertuples(index=False)]


@app.get("/stations/nearest", response_model=list[Station])
def nearest(
    snap: Snap,
    lat: Annotated[float, Query(ge=27.5, le=29.5)],
    lon: Annotated[float, Query(ge=76.0, le=78.5)],
    n: Annotated[int, Query(ge=1, le=10)] = 3,
) -> list[Station]:
    feed, _ = _feed(snap)
    df = nearest_stations(station_overview(snap), lat, lon, n)
    return [_station(r, feed) for r in df.itertuples(index=False)]


@app.get("/forecast/{station_id}", response_model=StationForecast)
def forecast(station_id: int, snap: Snap) -> StationForecast:
    df = station_overview(snap)
    row = df[df["location_id"] == station_id]
    if row.empty:
        raise HTTPException(status_code=404, detail=f"unknown station {station_id}")
    feed, _ = _feed(snap)
    station = _station(next(row.itertuples(index=False)), feed)

    fc = snap.table("forecasts_latest")
    fc = fc[fc["location_id"] == station_id].sort_values("horizon_h") if not fc.empty else fc
    forecasts = [
        Forecast(
            horizon_h=int(r.horizon_h),
            target_hour_start_utc=to_utc(r.target_hour_start_utc),
            target_hour_ist=ist(r.target_hour_start_utc),
            pm25_pred=float(r.pm25_pred),
            aqi_category_indicative=category_of(r.pm25_pred),
            is_stale_input=bool(r.is_stale_input),
            input_age_hours=None if pd.isna(r.input_age_hours) else int(r.input_age_hours),
            inputs_as_of_utc=to_utc(r.last_valid_hour_utc),
        )
        for r in fc.itertuples(index=False)
    ]
    warning = None
    if any(f.is_stale_input for f in forecasts):
        f = next(f for f in forecasts if f.is_stale_input)
        warning = (
            f"Forecasts use this station's last known readings from {ist(f.inputs_as_of_utc)} "
            f"({f.input_age_hours} h old) because new readings aren't arriving."
        )
    return StationForecast(
        station=station,
        model_version=None if fc.empty else str(fc["model_version"].iloc[0]),
        issued_at_utc=None if fc.empty else to_utc(fc["issue_time_utc"].iloc[0]),
        forecasts=forecasts,
        warning=warning,
    )

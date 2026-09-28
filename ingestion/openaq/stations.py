"""Pure station-selection rules for Delhi NCR discovery (no I/O, fully unit-tested).

Rules, all learnt from the Phase 0 spike (docs/station_coverage.md):
- Reference-grade only: OpenAQ's `isMonitor`, or a name ending in a known government agency.
  Newer OpenAQ entries for DPCC/UPPCB stations have `isMonitor=false` and provider "N/A".
- Active only: reported within the last 30 days (drops legacy duplicate locations).
- Live sensors only: stations keep dead legacy sensors next to the live ones, so a sensor is
  live if its latest reading is within 7 days of the station's own last reading.
- Co-located stations (< 100 m apart) are flagged so zone/city medians don't double-count.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ingestion.openaq.models import PM25_PARAMETER_ID, LatestReading, Location

AGENCIES = ("DPCC", "CPCB", "UPPCB", "HSPCB", "IMD", "IITM", "MHUA")
LOW_COST_PROVIDERS = ("AirGradient", "PurpleAir", "Clarity")
ACTIVE_WITHIN = timedelta(days=30)
LIVE_SENSOR_LAG = timedelta(days=7)
COLOCATED_WITHIN_M = 100.0

# Connaught Place: the centre used to split Delhi into Central + N/E/S/W zones.
DELHI_CENTRE = (28.6315, 77.2167)
CENTRAL_RADIUS_KM = 6.0

# Checked in order against the lower-cased station name; first match wins.
NCR_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("noida", "noida"),
    ("gurugram", "gurugram"),
    ("gurgaon", "gurugram"),
    ("manesar", "gurugram"),
    ("ggn", "gurugram"),
    ("faridabad", "faridabad"),
    ("ghaziabad", "ghaziabad"),
    ("loni", "ghaziabad"),
    ("khora", "ghaziabad"),
    ("modinagar", "ghaziabad"),
    ("bahadurgarh", "bahadurgarh"),
)

ZONE_NAMES = {
    "delhi_central": "Central Delhi",
    "delhi_north": "North Delhi",
    "delhi_east": "East Delhi",
    "delhi_south": "South Delhi",
    "delhi_west": "West Delhi",
    "noida": "Noida",
    "gurugram": "Gurugram",
    "ghaziabad": "Ghaziabad",
    "faridabad": "Faridabad",
    "bahadurgarh": "Bahadurgarh",
}

_AGENCY_RE = re.compile(r"-\s*(" + "|".join(AGENCIES) + r")\s*$")


@dataclass
class Station:
    location_id: int
    name: str
    agency: str | None
    provider: str | None
    latitude: float
    longitude: float
    timezone: str
    pm25_sensor_id: int
    # parameter key (e.g. "pm10", "co_ppb") -> live sensor id
    sensors: dict[str, int] = field(default_factory=dict)
    first_seen_utc: str | None = None
    last_seen_utc: str | None = None
    colocated_with: int | None = None
    zone: str = ""
    zone_reason: str = ""


def agency_from_name(name: str) -> str | None:
    m = _AGENCY_RE.search(name)
    return m.group(1) if m else None


def is_reference_grade(loc: Location) -> bool:
    provider = loc.provider.name if loc.provider else ""
    if provider in LOW_COST_PROVIDERS or loc.is_mobile:
        return False
    return loc.is_monitor or agency_from_name(loc.name) is not None


def is_candidate(loc: Location, now: datetime) -> bool:
    """Cheap pre-filter on /locations data, before spending a /latest call per station."""
    return (
        is_reference_grade(loc)
        and bool(loc.sensors_for(PM25_PARAMETER_ID))
        and loc.datetime_last is not None
        and loc.datetime_last.utc >= now - ACTIVE_WITHIN
    )


def live_sensor_ids(loc: Location, latest: list[LatestReading]) -> set[int]:
    if loc.datetime_last is None:
        return set()
    cutoff = loc.datetime_last.utc - LIVE_SENSOR_LAG
    return {r.sensors_id for r in latest if r.locations_id == loc.id and r.datetime.utc >= cutoff}


def build_station(loc: Location, latest: list[LatestReading]) -> Station | None:
    """None if the station has no live PM2.5 sensor."""
    live = live_sensor_ids(loc, latest)
    last_seen = {r.sensors_id: r.datetime.utc for r in latest}
    pm25 = [s.id for s in loc.sensors_for(PM25_PARAMETER_ID) if s.id in live]
    if not pm25:
        return None

    names = [s.parameter.name for s in loc.sensors if s.id in live]
    sensors: dict[str, int] = {}
    for s in loc.sensors:
        if s.id not in live:
            continue
        # Disambiguate parameters reported in two units (e.g. CO in µg/m³ and ppb).
        key = s.parameter.name if names.count(s.parameter.name) == 1 else _unit_key(s)
        sensors[key] = s.id

    return Station(
        location_id=loc.id,
        name=loc.name.strip(),
        agency=agency_from_name(loc.name),
        provider=loc.provider.name if loc.provider else None,
        latitude=loc.coordinates.latitude,
        longitude=loc.coordinates.longitude,
        timezone=loc.timezone,
        pm25_sensor_id=max(pm25, key=lambda i: last_seen[i]),
        sensors=dict(sorted(sensors.items())),
        first_seen_utc=_iso(loc.datetime_first.utc) if loc.datetime_first else None,
        last_seen_utc=_iso(loc.datetime_last.utc) if loc.datetime_last else None,
    )


def _unit_key(sensor) -> str:
    units = sensor.parameter.units.replace("µg/m³", "ugm3").replace("/", "").lower()
    return f"{sensor.parameter.name}_{units}"


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def bearing_deg(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    x = math.sin(lon2 - lon1) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(lon2 - lon1)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def assign_zone(name: str, lat: float, lon: float) -> tuple[str, str]:
    """(zone_id, reason). NCR cities by name keyword; Delhi by distance/bearing from CP."""
    lowered = name.lower()
    for keyword, zone in NCR_KEYWORDS:
        if keyword in lowered:
            return zone, f"name contains '{keyword}'"

    dist = haversine_km(DELHI_CENTRE, (lat, lon))
    if dist <= CENTRAL_RADIUS_KM:
        return "delhi_central", f"{dist:.1f} km from CP"
    b = bearing_deg(DELHI_CENTRE, (lat, lon))
    quadrant = (
        "north" if b >= 315 or b < 45 else "east" if b < 135 else "south" if b < 225 else "west"
    )
    return f"delhi_{quadrant}", f"{dist:.1f} km from CP, bearing {b:.0f}°"


def flag_colocated(stations: list[Station]) -> None:
    """Mark the higher-id station of any pair closer than COLOCATED_WITHIN_M."""
    ordered = sorted(stations, key=lambda s: s.location_id)
    for i, a in enumerate(ordered):
        for b in ordered[i + 1 :]:
            if b.colocated_with is not None:
                continue
            d_m = haversine_km((a.latitude, a.longitude), (b.latitude, b.longitude)) * 1000
            if d_m < COLOCATED_WITHIN_M:
                b.colocated_with = a.location_id


def select_stations(
    locations: list[Location], latest: dict[int, list[LatestReading]], now: datetime
) -> list[Station]:
    stations = []
    for loc in locations:
        if not is_candidate(loc, now):
            continue
        station = build_station(loc, latest.get(loc.id, []))
        if station is None:
            continue
        station.zone, station.zone_reason = assign_zone(
            station.name, station.latitude, station.longitude
        )
        stations.append(station)
    flag_colocated(stations)
    return sorted(stations, key=lambda s: s.location_id)

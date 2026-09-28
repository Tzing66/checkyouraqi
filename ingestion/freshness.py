"""Data freshness: is a station's latest reading live, delayed, inactive or offline, and is
the whole source down? Also the user-facing wording, so every surface says the same thing.

When there's no live data we still show the last reading, with when it was taken and why it
is old: an individual sensor going quiet vs. the upstream service being down.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from ingestion.settings import CONFIG_DIR

IST = ZoneInfo("Asia/Kolkata")


class StationStatus(StrEnum):
    LIVE = "live"
    DELAYED = "delayed"
    INACTIVE = "inactive"
    OFFLINE = "offline"
    NO_DATA = "no_data"


class FeedStatus(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    OUTAGE = "outage"


@dataclass(frozen=True)
class Thresholds:
    live_max: timedelta
    delayed_max: timedelta
    inactive_max: timedelta
    outage_share: float
    degraded_share: float

    @classmethod
    def load(cls, path: Path = CONFIG_DIR / "freshness.yaml") -> Thresholds:
        cfg = yaml.safe_load(path.read_text())
        return cls(
            live_max=timedelta(hours=cfg["station"]["live_max_hours"]),
            delayed_max=timedelta(hours=cfg["station"]["delayed_max_hours"]),
            inactive_max=timedelta(days=cfg["station"]["inactive_max_days"]),
            outage_share=cfg["feed"]["outage_min_share_not_live"],
            degraded_share=cfg["feed"]["degraded_min_share_not_live"],
        )


@dataclass(frozen=True)
class FeedReport:
    status: FeedStatus
    share_not_live: float
    # Most recent reading from any station: "the service has had nothing new since ...".
    newest_reading_utc: datetime | None


def classify(last_reading_utc: datetime | None, now: datetime, t: Thresholds) -> StationStatus:
    if last_reading_utc is None:
        return StationStatus.NO_DATA
    age = now - last_reading_utc
    if age <= t.live_max:
        return StationStatus.LIVE
    if age <= t.delayed_max:
        return StationStatus.DELAYED
    if age <= t.inactive_max:
        return StationStatus.INACTIVE
    return StationStatus.OFFLINE


def feed_report(
    last_readings: Mapping[int, datetime | None], now: datetime, t: Thresholds
) -> FeedReport:
    if not last_readings:
        return FeedReport(FeedStatus.OUTAGE, 1.0, None)
    statuses = [classify(dt, now, t) for dt in last_readings.values()]
    share = sum(s != StationStatus.LIVE for s in statuses) / len(statuses)
    known = [dt for dt in last_readings.values() if dt is not None]
    status = (
        FeedStatus.OUTAGE
        if share >= t.outage_share
        else FeedStatus.DEGRADED
        if share >= t.degraded_share
        else FeedStatus.OK
    )
    return FeedReport(status, share, max(known) if known else None)


def format_ist(dt: datetime) -> str:
    return dt.astimezone(IST).strftime("%-d %b, %H:%M IST")


def humanize_age(age: timedelta) -> str:
    hours = age.total_seconds() / 3600
    if hours < 1:
        return f"{max(int(age.total_seconds() // 60), 1)} min ago"
    if hours < 48:
        return f"{int(hours)} h ago"
    return f"{int(hours // 24)} days ago"


def station_message(
    status: StationStatus, last_reading_utc: datetime | None, now: datetime, feed: FeedReport
) -> str | None:
    """Banner text for a station, or None when the data is live and needs no caveat."""
    if status == StationStatus.LIVE:
        return None
    if status == StationStatus.NO_DATA or last_reading_utc is None:
        return "No readings available for this station yet."
    when = f"{format_ist(last_reading_utc)} ({humanize_age(now - last_reading_utc)})"
    if feed.status == FeedStatus.OUTAGE:
        return (
            f"The air quality data service isn't sending new readings. "
            f"Showing this station's last reading from {when}."
        )
    if status == StationStatus.DELAYED:
        return f"Readings are delayed. Last reading: {when}."
    if status == StationStatus.INACTIVE:
        return f"This station's sensor appears inactive. Showing its last reading from {when}."
    return f"This station has been offline since {when} and is excluded from area averages."


def feed_message(feed: FeedReport) -> str | None:
    """Site-wide banner, or None when the source is healthy."""
    if feed.status == FeedStatus.OK:
        return None
    since = format_ist(feed.newest_reading_utc) if feed.newest_reading_utc else "unknown"
    if feed.status == FeedStatus.OUTAGE:
        return (
            f"Live updates are paused: our data source (OpenAQ / CPCB) has sent no new readings "
            f"since {since}. You're seeing the last available data for each station."
        )
    return f"Some stations ({feed.share_not_live:.0%}) are reporting late or not at all."

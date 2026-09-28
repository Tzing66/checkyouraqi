"""Open-Meteo hourly responses (forecast, archive and previous-runs share this shape)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator


@dataclass(frozen=True)
class WeatherPoint:
    """Where we ask for weather. One per zone (see points.py)."""

    id: str
    latitude: float
    longitude: float


class HourlySeries(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # Open-Meteo snaps the request to its grid, so these differ from the requested point.
    latitude: float
    longitude: float
    elevation: float | None = None
    utc_offset_seconds: int
    hourly_units: dict[str, str]
    hourly: dict[str, list[Any]]

    @model_validator(mode="after")
    def _check(self) -> HourlySeries:
        if self.utc_offset_seconds != 0:
            raise ValueError("expected UTC (timezone=GMT) responses")
        if "time" not in self.hourly:
            raise ValueError("hourly block has no 'time' array")
        n = len(self.hourly["time"])
        uneven = [k for k, v in self.hourly.items() if len(v) != n]
        if uneven:
            raise ValueError(f"hourly arrays differ in length from 'time': {uneven}")
        return self

    @property
    def variables(self) -> list[str]:
        return [k for k in self.hourly if k != "time"]

    def rows(self) -> list[dict[str, Any]]:
        """One dict per hour: {'time_utc': datetime, <variable>: value, ...}."""
        times = [datetime.fromisoformat(t).replace(tzinfo=UTC) for t in self.hourly["time"]]
        return [
            {"time_utc": t, **{v: self.hourly[v][i] for v in self.variables}}
            for i, t in enumerate(times)
        ]

"""One VIIRS fire detection from a FIRMS area CSV (NRT and SP share these columns)."""

from __future__ import annotations

from datetime import UTC, date, datetime

from pydantic import BaseModel, ConfigDict, field_validator

VIIRS_CONFIDENCE = {"l": "low", "n": "nominal", "h": "high"}


class FirePoint(BaseModel):
    model_config = ConfigDict(extra="ignore")

    latitude: float
    longitude: float
    bright_ti4: float | None = None
    frp: float | None = None  # fire radiative power, MW
    confidence: str
    acq_date: date
    acq_time: int  # HHMM in UTC, e.g. 826 = 08:26
    satellite: str
    instrument: str
    daynight: str
    version: str

    @field_validator("confidence")
    @classmethod
    def _confidence(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in VIIRS_CONFIDENCE:
            raise ValueError(f"unexpected VIIRS confidence {v!r}")
        return v

    @field_validator("acq_time")
    @classmethod
    def _hhmm(cls, v: int) -> int:
        if not (0 <= v // 100 <= 23 and 0 <= v % 100 <= 59):
            raise ValueError(f"acq_time {v} is not HHMM")
        return v

    @property
    def acquired_at_utc(self) -> datetime:
        return datetime(
            self.acq_date.year,
            self.acq_date.month,
            self.acq_date.day,
            self.acq_time // 100,
            self.acq_time % 100,
            tzinfo=UTC,
        )

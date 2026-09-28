"""Pydantic models for the OpenAQ v3 responses we use.

Only the fields we rely on are modelled; unknown fields are ignored so additive API changes
don't break ingestion. Bronze keeps the raw JSON, so nothing is lost by this.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator
from pydantic.alias_generators import to_camel

PM25_PARAMETER_ID = 2


class _Model(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")


class DateTimePair(_Model):
    utc: datetime
    local: str | None = None


class Parameter(_Model):
    id: int
    name: str
    units: str
    display_name: str | None = None


class Sensor(_Model):
    id: int
    name: str
    parameter: Parameter


class Coordinates(_Model):
    latitude: float
    longitude: float


class Ref(_Model):
    id: int
    name: str


class Location(_Model):
    id: int
    name: str
    locality: str | None = None
    timezone: str
    country: Ref | None = None
    owner: Ref | None = None
    provider: Ref | None = None
    is_mobile: bool = False
    is_monitor: bool = False
    coordinates: Coordinates
    sensors: list[Sensor] = []
    datetime_first: DateTimePair | None = None
    datetime_last: DateTimePair | None = None

    def sensors_for(self, parameter_id: int) -> list[Sensor]:
        # Live stations often also list long-dead legacy sensors for the same parameter.
        return [s for s in self.sensors if s.parameter.id == parameter_id]


class LatestReading(_Model):
    datetime: DateTimePair
    value: float | None
    coordinates: Coordinates | None = None
    sensors_id: int
    locations_id: int


class Period(_Model):
    label: str
    interval: str
    datetime_from: DateTimePair
    datetime_to: DateTimePair


class Coverage(_Model):
    expected_count: int | None = None
    observed_count: int | None = None
    percent_complete: float | None = None


class Measurement(_Model):
    """A raw reading (/measurements) or an hourly aggregate (/hours)."""

    value: float | None
    parameter: Parameter
    period: Period
    coverage: Coverage | None = None


class Meta(_Model):
    page: int
    limit: int
    # OpenAQ returns an int when it knows the total, or a string like ">1000" when it doesn't.
    found: int | str | None = None

    @field_validator("found", mode="before")
    @classmethod
    def _found(cls, v: object) -> object:
        if isinstance(v, str) and v.isdigit():
            return int(v)
        return v

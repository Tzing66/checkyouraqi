"""Data access for the dashboard: the public/ snapshots only, never Athena (plan §2).

Reads public/manifest.json, then each dataset's Parquet files, into pandas. Two sources:
  - S3 with AWS credentials (local development, profile from .env), or
  - plain HTTPS (PUBLIC_BASE_URL, e.g. once the bucket's public/ prefix is readable in Phase 5).
Timestamps are UTC throughout; the UI converts to IST at display time.
"""

from __future__ import annotations

import io
import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Protocol

import pandas as pd

MANIFEST_KEY = "public/manifest.json"


class Source(Protocol):
    def get(self, key: str) -> bytes: ...


class S3Source:
    def __init__(self, bucket: str, client: Any) -> None:
        self.bucket = bucket
        self._s3 = client

    def get(self, key: str) -> bytes:
        return self._s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()


class HttpSource:
    """Public HTTPS reads. One pooled client (reused connections instead of a TLS handshake
    per file) and a few retries on dropped connections, which parallel fetches can trigger."""

    def __init__(self, base_url: str, *, client=None, retries: int = 3) -> None:
        import httpx

        self.base_url = base_url.rstrip("/")
        self._http = client or httpx.Client(
            timeout=30, limits=httpx.Limits(max_connections=8, max_keepalive_connections=8)
        )
        self._retries = retries

    def get(self, key: str) -> bytes:
        import time

        import httpx

        for attempt in range(self._retries):
            try:
                resp = self._http.get(f"{self.base_url}/{key}")
                resp.raise_for_status()
                return resp.content
            except (httpx.TransportError, httpx.HTTPStatusError) as e:
                status = getattr(getattr(e, "response", None), "status_code", 500)
                if attempt == self._retries - 1 or status < 500 and status != 429:
                    raise
                time.sleep(0.5 * 2**attempt)
        raise RuntimeError("unreachable")


def default_source() -> Source:
    base = os.environ.get("PUBLIC_BASE_URL")
    if base:
        return HttpSource(base)
    import boto3

    from ingestion.settings import Settings

    s = Settings()
    session = boto3.Session(profile_name=s.aws_profile, region_name=s.aws_region)
    return S3Source(s.data_bucket, session.client("s3"))


@dataclass
class Snapshot:
    """Everything the dashboard shows, loaded once and cached by the UI layer."""

    exported_at: pd.Timestamp
    tables: dict[str, pd.DataFrame]
    files: dict[str, Any] = field(default_factory=dict)

    def table(self, name: str) -> pd.DataFrame:
        return self.tables.get(name, pd.DataFrame())


def _read_parquet(parts: list[bytes]) -> pd.DataFrame:
    frames = [pd.read_parquet(io.BytesIO(b)) for b in parts]
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    for c in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[c]):
            df[c] = pd.to_datetime(df[c], utc=True)
    return df


def _safe_json(source: Source, key: str) -> Any:
    try:
        return json.loads(source.get(key))
    except Exception:  # an optional file (e.g. no drift report yet) must not break the UI
        return None


def load_snapshot(source: Source, *, workers: int = 8) -> Snapshot:
    """Fetch everything in parallel (a dozen small objects): the cold page load is dominated
    by request latency, not bytes."""
    manifest = json.loads(source.get(MANIFEST_KEY))
    datasets = manifest["datasets"]
    files_spec = manifest.get("files", {})
    with ThreadPoolExecutor(max_workers=workers) as pool:
        parts = {
            name: [pool.submit(source.get, k) for k in spec.get("keys", [])]
            for name, spec in datasets.items()
        }
        extra = {name: pool.submit(_safe_json, source, key) for name, key in files_spec.items()}
        tables = {name: _read_parquet([f.result() for f in fs]) for name, fs in parts.items()}
        files = {name: f.result() for name, f in extra.items()}
    return Snapshot(pd.Timestamp(manifest["exported_at"]), tables, files)


# --- derived views used by several pages ------------------------------------------------


def station_overview(snap: Snapshot) -> pd.DataFrame:
    """One row per station: identity, zone, status, last reading, latest 24h average."""
    stations = snap.table("dim_station")
    status = snap.table("station_status")
    zones = snap.table("dim_zone")
    hourly = snap.table("aqi_hourly_recent")
    if stations.empty:
        return pd.DataFrame()
    latest = pd.DataFrame(columns=["location_id", "pm25_24h", "aqi_category"])
    if not hourly.empty:
        valid = hourly.dropna(subset=["pm25_24h"]).sort_values("hour_start_utc")
        latest = (
            valid.groupby("location_id")
            .tail(1)[["location_id", "pm25_24h", "aqi_category", "hour_start_utc"]]
            .rename(columns={"hour_start_utc": "pm25_24h_as_of_utc"})
        )
    out = stations.merge(
        status[
            [
                "location_id",
                "status",
                "last_pm25_reading_utc",
                "last_pm25_value",
                "last_checked_utc",
            ]
        ],
        on="location_id",
        how="left",
    ).merge(latest, on="location_id", how="left")
    if not zones.empty:
        out = out.merge(zones[["zone_id", "zone_name"]], on="zone_id", how="left")
    return out.sort_values("station_name", ignore_index=True)


def zone_summary(snap: Snapshot) -> pd.DataFrame:
    z = snap.table("zone_hourly_recent")
    zones = snap.table("dim_zone")
    if z.empty:
        return pd.DataFrame()
    latest = z.dropna(subset=["pm25_24h_median"]).sort_values("hour_start_utc")
    latest = latest.groupby("zone_id").tail(1)
    return latest.merge(zones[["zone_id", "zone_name"]], on="zone_id", how="left").sort_values(
        "pm25_24h_median", ascending=False, ignore_index=True
    )

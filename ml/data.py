"""Load the training table (aqi_gold.fct_features_hourly) as a local Parquet cache.

The table is UNLOADed from Athena to s3://<bucket>/ml/features/run=<id>/ (Parquet, millisecond
timestamps), then downloaded to data/ml/features/<id>/. Later runs reuse the newest local
copy unless refresh=True, which keeps training reproducible and costs nothing.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

import boto3
import pandas as pd

from ingestion.settings import ROOT, Settings
from lakehouse.athena import run_query
from lakehouse.export_public import Dataset, columns_sql, select_sql, unload_sql

log = logging.getLogger(__name__)

CACHE_DIR = ROOT / "data" / "ml" / "features"
FEATURES_TABLE = "aqi_gold.fct_features_hourly"


def _latest_cache() -> Path | None:
    runs = sorted(p for p in CACHE_DIR.glob("*") if p.is_dir() and any(p.glob("*.parquet")))
    return runs[-1] if runs else None


def fetch_features(settings: Settings) -> Path:
    session = boto3.Session(profile_name=settings.aws_profile, region_name=settings.aws_region)
    athena, s3 = session.client("athena"), session.client("s3")
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    ds = Dataset("features", FEATURES_TABLE, "training table", "where target_pm25 is not null")

    info = run_query(athena, columns_sql((ds,)))
    columns = [(c, t) for _, c, t in info.rows[1:]]
    prefix = f"ml/features/run={run_id}/"
    result = run_query(
        athena,
        unload_sql(select_sql(ds, columns), f"s3://{settings.data_bucket}/{prefix}"),
        fetch_rows=False,
    )
    log.info("unloaded features (%.1f MB scanned)", result.bytes_scanned / 1e6)

    out = CACHE_DIR / run_id
    out.mkdir(parents=True, exist_ok=True)
    listed = s3.list_objects_v2(Bucket=settings.data_bucket, Prefix=prefix)
    for i, obj in enumerate(listed.get("Contents", [])):
        s3.download_file(settings.data_bucket, obj["Key"], str(out / f"part-{i:04d}.parquet"))
    return out


def load_features(*, refresh: bool = False, settings: Settings | None = None) -> pd.DataFrame:
    path = None if refresh else _latest_cache()
    if path is None:
        path = fetch_features(settings or Settings())
    log.info("loading features from %s", path)
    df = pd.read_parquet(path)
    for c in ("issue_hour_start_utc", "issue_time_utc", "target_hour_start_utc"):
        df[c] = pd.to_datetime(df[c])
    df.attrs["source"] = str(path.relative_to(ROOT))
    return df.sort_values(["issue_time_utc", "location_id", "horizon_h"], ignore_index=True)

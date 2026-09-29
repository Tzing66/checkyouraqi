"""Export small gold snapshots to s3://<bucket>/public/ as Parquet for DuckDB consumers.

The dashboard, API and assistant never query Athena (plan §2). They read these snapshots.

Layout, so readers never see a half-written export:
    public/<dataset>/run=<run_id>/*.parquet      written by Athena UNLOAD
    public/manifest.json                          points each dataset at its current run
The manifest is written only after every dataset has unloaded. Older runs are then pruned,
keeping the previous one so a reader holding the old manifest still finds its files.

"Recent" windows are relative to the newest data, not the clock, so during a source outage
the dashboard still has the last 30 days of real data to show (with the stale-data banner).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from lakehouse.athena import QueryResult, run_query

MANIFEST_KEY = "public/manifest.json"
KEEP_RUNS = 2
RECENT_DAYS = 30


@dataclass(frozen=True)
class Dataset:
    name: str
    table: str  # schema.table
    description: str
    where: str = ""


def _recent(table: str, value_col: str, days: int = RECENT_DAYS) -> str:
    return (
        f"where hour_start_utc >= (select max(hour_start_utc) - interval '{days}' day "
        f"from {table} where {value_col} is not null)"
    )


DATASETS: tuple[Dataset, ...] = (
    Dataset("dim_station", "aqi_gold.dim_station", "Stations + quality stats"),
    Dataset("dim_zone", "aqi_gold.dim_zone", "Zones"),
    Dataset("dim_city", "aqi_gold.dim_city", "Cities"),
    Dataset(
        "station_status",
        "aqi_gold.fct_station_status",
        "Current freshness per station (stale-data badges)",
    ),
    Dataset(
        "feed_status",
        "aqi_gold.fct_feed_status",
        "Hourly source status, last 30 days by clock (shows outages)",
        f"where hour_start_utc >= current_timestamp - interval '{RECENT_DAYS}' day",
    ),
    Dataset(
        "aqi_hourly_recent",
        "aqi_gold.fct_aqi_hourly",
        "Station PM2.5 + 24h avg + AQI category, last 30 days of data",
        _recent("aqi_gold.fct_aqi_hourly", "pm25"),
    ),
    Dataset(
        "zone_hourly_recent",
        "aqi_gold.agg_zone_hourly",
        "Zone medians, last 30 days of data",
        _recent("aqi_gold.agg_zone_hourly", "pm25_median"),
    ),
    Dataset(
        "city_hourly_recent",
        "aqi_gold.agg_city_hourly",
        "City medians, last 30 days of data",
        _recent("aqi_gold.agg_city_hourly", "pm25_median"),
    ),
    Dataset("city_daily", "aqi_gold.agg_city_daily", "City daily, full history"),
    Dataset(
        "fires_daily",
        "aqi_silver.int_fires_upwind_daily",
        "Fire counts by sector/band, last 120 days",
        "where acq_date >= current_date - interval '120' day",
    ),
)


def columns_sql(datasets: tuple[Dataset, ...]) -> str:
    tables = ", ".join(f"'{d.table}'" for d in datasets)
    return (
        "select table_schema || '.' || table_name, column_name, data_type "
        "from information_schema.columns "
        f"where table_schema || '.' || table_name in ({tables}) "
        "order by table_schema, table_name, ordinal_position"
    )


def select_sql(ds: Dataset, columns: list[tuple[str, str]]) -> str:
    """Explicit column list; timestamps cast to millisecond precision, the only precision
    Athena UNLOAD can write to Parquet (our Iceberg tables store microseconds)."""
    if not columns:
        raise ValueError(f"no columns found for {ds.table}")
    cols = ", ".join(
        f'cast("{c}" as timestamp(3)) as "{c}"' if t.startswith("timestamp") else f'"{c}"'
        for c, t in columns
    )
    return f"select {cols} from {ds.table} {ds.where}".strip()


def unload_sql(select_sql: str, location: str) -> str:
    return (
        f"unload ({select_sql.strip()}) to '{location}' "
        "with (format = 'PARQUET', compression = 'SNAPPY')"
    )


def run_id_for(now: datetime) -> str:
    return now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _list_prefixes(s3: Any, bucket: str, prefix: str) -> list[str]:
    resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix, Delimiter="/")
    return sorted(p["Prefix"] for p in resp.get("CommonPrefixes", []))


def _delete_prefix(s3: Any, bucket: str, prefix: str) -> None:
    resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
    keys = [{"Key": o["Key"]} for o in resp.get("Contents", [])]
    if keys:
        s3.delete_objects(Bucket=bucket, Delete={"Objects": keys, "Quiet": True})


def export(
    athena: Any,
    s3: Any,
    bucket: str,
    *,
    now: datetime | None = None,
    datasets: tuple[Dataset, ...] = DATASETS,
    query: Callable[..., QueryResult] = run_query,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    run_id = run_id_for(now)
    manifest: dict[str, Any] = {"exported_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "datasets": {}}
    total_scanned = 0

    info = query(athena, columns_sql(datasets))
    total_scanned += info.bytes_scanned
    columns: dict[str, list[tuple[str, str]]] = {}
    for table, col, dtype in info.rows[1:]:  # first row is the header
        columns.setdefault(table, []).append((col, dtype))

    for ds in datasets:
        prefix = f"public/{ds.name}/run={run_id}/"
        _delete_prefix(s3, bucket, prefix)  # UNLOAD requires an empty target
        sql = unload_sql(select_sql(ds, columns.get(ds.table, [])), f"s3://{bucket}/{prefix}")
        result = query(athena, sql, fetch_rows=False)
        total_scanned += result.bytes_scanned
        manifest["datasets"][ds.name] = {
            "path": f"s3://{bucket}/{prefix}",
            "glob": f"s3://{bucket}/{prefix}*",
            "description": ds.description,
        }

    # Publish atomically: readers switch to the new run only once everything is written.
    s3.put_object(
        Bucket=bucket,
        Key=MANIFEST_KEY,
        Body=json.dumps(manifest, indent=2).encode(),
        ContentType="application/json",
        CacheControl="max-age=300",
    )

    for ds in datasets:
        runs = _list_prefixes(s3, bucket, f"public/{ds.name}/")
        for old in runs[:-KEEP_RUNS]:
            _delete_prefix(s3, bucket, old)

    manifest["bytes_scanned"] = total_scanned
    return manifest

"""Daily model monitoring (plan §7.9): feature drift (Evidently) and live forecast accuracy.

- Drift: features actually served by the predict DAG over the last 7 days (saved hourly to
  bronze/served_features/) vs a sample of the training table. Written as a static Evidently
  HTML report to s3://<bucket>/reports/drift/dt=YYYY-MM-DD/drift.html (+ latest.html), and a
  small summary to public/drift_summary.json for the dashboard.
- Accuracy: MAE / bias / category accuracy per horizon from aqi_gold.fct_forecast_accuracy
  over the last 7 and 30 days -> public/accuracy_summary.json. Empty while the source is down.
"""

from __future__ import annotations

import gzip
import io
import json
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pandas as pd

from lakehouse.athena import run_query
from ml.features import NUMERIC

DRIFT_WINDOW_DAYS = 7
# One hour of served features vs 180 days of training data always "drifts" (a single hour's
# weather/calendar/fires); only report a verdict once the window covers enough hours.
MIN_SERVED_HOURS = 24
REFERENCE_DAYS = 180
SERVED_PREFIX = "bronze/served_features"


def served_features_key(issue_time: pd.Timestamp) -> str:
    t = pd.Timestamp(issue_time)
    return f"{SERVED_PREFIX}/dt={t:%Y-%m-%d}/hour={t:%H}/features.json.gz"


def served_features_payload(features: pd.DataFrame) -> list[dict[str, Any]]:
    """The live feature rows exactly as the model saw them (JSON-safe)."""
    df = features.copy()
    for c in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[c]):
            df[c] = df[c].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return json.loads(df.to_json(orient="records"))


def load_served_features(s3: Any, bucket: str, since: date) -> pd.DataFrame:
    frames = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=f"{SERVED_PREFIX}/"):
        for obj in page.get("Contents", []):
            m = re.search(r"dt=(\d{4}-\d{2}-\d{2})/", obj["Key"])
            if not m or date.fromisoformat(m.group(1)) < since:
                continue
            raw = s3.get_object(Bucket=bucket, Key=obj["Key"])["Body"].read()
            frames.append(pd.DataFrame(json.loads(gzip.decompress(raw))))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def reference_sql(days: int = REFERENCE_DAYS) -> str:
    cols = ", ".join(f'"{c}"' for c in NUMERIC)
    return (
        f"select {cols} from aqi_gold.fct_features_hourly "
        f"where horizon_h = 24 and issue_hour_start_utc >= "
        f"(select max(issue_hour_start_utc) - interval '{days}' day "
        f"from aqi_gold.fct_features_hourly) "
        f"and (location_id + hour(issue_hour_start_utc)) % 5 = 0"
    )


def load_reference(
    athena: Any, s3: Any, bucket: str, *, now: datetime | None = None
) -> pd.DataFrame:
    """A ~20k-row sample of recent training features, via UNLOAD to Parquet."""
    run = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    prefix = f"ml/reference/run={run}/"
    run_query(
        athena,
        f"unload ({reference_sql()}) to 's3://{bucket}/{prefix}' "
        "with (format = 'PARQUET', compression = 'SNAPPY')",
        fetch_rows=False,
    )
    frames = []
    for obj in s3.list_objects_v2(Bucket=bucket, Prefix=prefix).get("Contents", []):
        body = s3.get_object(Bucket=bucket, Key=obj["Key"])["Body"].read()
        frames.append(pd.read_parquet(io.BytesIO(body)))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _drifted(metric_name: str, value: float) -> bool:
    """Evidently ValueDrift: p-value methods drift when value < threshold, distance methods
    (e.g. Wasserstein) when value > threshold."""
    threshold = float(re.search(r"threshold=([0-9.]+)", metric_name).group(1))
    return value < threshold if "p_value" in metric_name else value > threshold


def summarise_drift(snapshot: dict[str, Any]) -> dict[str, Any]:
    columns = {}
    share = None
    for m in snapshot["metrics"]:
        name = m.get("metric_name", "")
        if name.startswith("DriftedColumnsCount"):
            share = float(m["value"]["share"])
        elif name.startswith("ValueDrift("):
            col = re.search(r"column=([^,]+)", name).group(1)
            method = re.search(r"method=([^,]+)", name).group(1)
            value = float(m["value"])
            columns[col] = {
                "method": method,
                "value": round(value, 4),
                "drifted": _drifted(name, value),
            }
    drifted = sorted(c for c, v in columns.items() if v["drifted"])
    return {
        "share_drifted": share if share is not None else len(drifted) / max(len(columns), 1),
        "drifted_columns": drifted,
        "columns": columns,
    }


def drift_report(reference: pd.DataFrame, current: pd.DataFrame) -> tuple[str, dict[str, Any]]:
    from evidently import Report
    from evidently.presets import DataDriftPreset

    cols = [c for c in NUMERIC if c in reference.columns and c in current.columns]
    ref = reference[cols].apply(pd.to_numeric, errors="coerce")
    cur = current[cols].apply(pd.to_numeric, errors="coerce")
    snapshot = Report([DataDriftPreset()]).run(current_data=cur, reference_data=ref)
    return snapshot.get_html_str(as_iframe=False), summarise_drift(snapshot.dict())


def accuracy_sql() -> str:
    return """
        select horizon_h, window_days, count(*) as n,
               avg(abs_error) as mae, avg(error) as bias,
               avg(cast(category_correct as double)) as category_accuracy,
               count_if(is_stale_input) as n_stale_inputs
        from aqi_gold.fct_forecast_accuracy
        cross join unnest(array[7, 30]) as w (window_days)
        where target_hour_start_utc >= current_timestamp - w.window_days * interval '1' day
        group by 1, 2 order by 1, 2"""


def accuracy_summary(athena: Any) -> dict[str, Any]:
    header, *rows = run_query(athena, accuracy_sql()).rows
    out: dict[str, Any] = {}
    for r in rows:
        rec = dict(zip(header, r, strict=True))
        h, w = rec.pop("horizon_h"), rec.pop("window_days")
        out.setdefault(h, {})[f"last_{w}d"] = {
            k: (None if v == "" else round(float(v), 3) if k != "n" else int(v))
            for k, v in rec.items()
        }
    return {"has_live_accuracy": bool(out), "horizons": out}


def monitor(athena: Any, s3: Any, bucket: str, run_day: date) -> dict[str, Any]:
    generated = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    since = run_day - timedelta(days=DRIFT_WINDOW_DAYS)
    current = load_served_features(s3, bucket, since)
    if not current.empty and "horizon_h" in current:
        current = current[current["horizon_h"] == 24]

    drift: dict[str, Any] = {
        "generated_at": generated,
        "window_days": DRIFT_WINDOW_DAYS,
        "rows_current": int(len(current)),
    }
    served_hours = int(current["issue_time_utc"].nunique()) if not current.empty else 0
    drift["served_hours"] = served_hours
    if current.empty:
        drift["status"] = "no served features in window"
    elif served_hours < MIN_SERVED_HOURS:
        drift["status"] = (
            f"insufficient data: {served_hours} served hour(s) in the window, "
            f"need {MIN_SERVED_HOURS} for a meaningful drift verdict"
        )
    else:
        reference = load_reference(athena, s3, bucket)
        html, summary = drift_report(reference, current)
        drift |= {"status": "ok", "rows_reference": int(len(reference)), **summary}
        for key in (f"reports/drift/dt={run_day:%Y-%m-%d}/drift.html", "reports/drift/latest.html"):
            s3.put_object(Bucket=bucket, Key=key, Body=html.encode(), ContentType="text/html")
        drift["report_key"] = "reports/drift/latest.html"

    accuracy = {"generated_at": generated, **accuracy_summary(athena)}
    for key, body in (
        ("public/drift_summary.json", drift),
        ("public/accuracy_summary.json", accuracy),
    ):
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=json.dumps(body, indent=1).encode(),
            ContentType="application/json",
        )
    return {
        "drift_share": drift.get("share_drifted"),
        "drift_status": drift["status"],
        "has_live_accuracy": accuracy["has_live_accuracy"],
    }

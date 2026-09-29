"""Live prediction: aqi_gold.fct_features_live -> production models -> bronze predictions.

    bronze/predictions/dt=YYYY-MM-DD/hour=HH/predictions.json.gz   (one file per issue hour)

Every station is forecast even when its PM2.5 inputs are stale (owner decision 2026-09-29):
the row carries input_age_hours / is_stale_input so every surface can warn. dbt turns these
files into aqi_gold.fct_forecasts.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from lakehouse.athena import run_query
from ml.features import decode_target, level_base, to_model_frame
from ml.registry import ProductionModel

log = logging.getLogger(__name__)

LIVE_FEATURES_SQL = "select * from aqi_gold.fct_features_live"


def _iso(ts: Any) -> str | None:
    if ts is None or pd.isna(ts):
        return None
    return pd.Timestamp(ts).tz_localize(None).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_live_features(athena: Any) -> pd.DataFrame:
    """Fetch the (small) live feature table via Athena query results."""
    result = run_query(athena, LIVE_FEATURES_SQL)
    header, *rows = result.rows
    df = pd.DataFrame(rows, columns=header).replace("", None)
    for c in df.columns:
        if c.endswith("_utc") or c.startswith("audit_"):
            df[c] = pd.to_datetime(df[c])
        elif c in ("zone_id", "city_id"):
            continue
        elif c in ("target_is_festival", "target_is_weekend", "is_stale_input"):
            df[c] = df[c].map({"true": True, "false": False})
        else:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def predict_frame(features: pd.DataFrame, models: dict[int, ProductionModel]) -> pd.DataFrame:
    out = []
    for h, model in sorted(models.items()):
        d = features[features["horizon_h"] == h]
        if d.empty:
            continue
        x = to_model_frame(d)[model.features]
        pred = decode_target(model.booster.predict(x), level_base(d), model.target_transform)
        out.append(
            pd.DataFrame(
                {
                    "location_id": d["location_id"].astype("int64").values,
                    "zone_id": d["zone_id"].values,
                    "horizon_h": h,
                    "issue_time_utc": d["issue_time_utc"].values,
                    "target_hour_start_utc": d["target_hour_start_utc"].values,
                    "pm25_pred": pred.clip(lower=0).round(1).values,
                    "input_age_hours": d["input_age_hours"].values,
                    "is_stale_input": d["is_stale_input"].astype(bool).values,
                    "last_valid_hour_utc": d["last_valid_hour_utc"].values,
                    "model_version": model.version,
                }
            )
        )
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def to_payload(preds: pd.DataFrame, predicted_at: datetime) -> dict:
    return {
        "predicted_at": predicted_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "predictions": [
            {
                "location_id": int(r.location_id),
                "zone_id": r.zone_id,
                "horizon_h": int(r.horizon_h),
                "issue_time_utc": _iso(r.issue_time_utc),
                "target_hour_start_utc": _iso(r.target_hour_start_utc),
                "pm25_pred": float(r.pm25_pred),
                "input_age_hours": None if pd.isna(r.input_age_hours) else int(r.input_age_hours),
                "is_stale_input": bool(r.is_stale_input),
                "last_valid_hour_utc": _iso(r.last_valid_hour_utc),
                "model_version": r.model_version,
            }
            for r in preds.itertuples(index=False)
        ],
    }


def prediction_key(issue_time: pd.Timestamp) -> str:
    t = pd.Timestamp(issue_time)
    return f"bronze/predictions/dt={t:%Y-%m-%d}/hour={t:%H}/predictions.json.gz"

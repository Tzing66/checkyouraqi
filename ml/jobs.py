"""ML entry points for Airflow (no Airflow imports, runnable standalone)."""

from __future__ import annotations

import logging
import os
from datetime import UTC, date, datetime
from typing import Any

import boto3

from ingestion.settings import Settings
from ingestion.writers import S3Writer
from ml.monitor import monitor, served_features_key, served_features_payload
from ml.predict import predict_frame, prediction_key, read_live_features, to_payload
from ml.registry import load_production

log = logging.getLogger(__name__)


def predict_live() -> dict[str, Any]:
    os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
    s = Settings()
    session = boto3.Session(profile_name=s.aws_profile, region_name=s.aws_region)
    features = read_live_features(session.client("athena"))
    models = load_production(session.client("s3"), s.data_bucket)
    preds = predict_frame(features, models)
    if preds.empty:
        raise RuntimeError("no live features to predict from (is fct_features_live empty?)")
    issue = preds["issue_time_utc"].max()
    writer = S3Writer(s.data_bucket, session.client("s3"))
    uri = writer.put_json(prediction_key(issue), to_payload(preds, datetime.now(UTC)))
    # Keep the exact features served, for drift monitoring (ml/monitor.py).
    writer.put_json(served_features_key(issue), served_features_payload(features))
    stale = int(preds["is_stale_input"].sum())
    log.info(
        "wrote %d predictions for issue %s (%d with stale inputs) -> %s",
        len(preds),
        issue,
        stale,
        uri,
    )
    return {"issue_time_utc": str(issue), "predictions": len(preds), "stale": stale}


def monitor_daily(run_day: date) -> dict[str, Any]:
    s = Settings()
    session = boto3.Session(profile_name=s.aws_profile, region_name=s.aws_region)
    result = monitor(session.client("athena"), session.client("s3"), s.data_bucket, run_day)
    log.info("monitor %s: %s", run_day, result)
    return result

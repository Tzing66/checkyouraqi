"""ML entry points for Airflow (no Airflow imports, runnable standalone)."""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

import boto3

from ingestion.settings import Settings
from ingestion.writers import S3Writer
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
    uri = S3Writer(s.data_bucket, session.client("s3")).put_json(
        prediction_key(issue), to_payload(preds, datetime.now(UTC))
    )
    stale = int(preds["is_stale_input"].sum())
    log.info(
        "wrote %d predictions for issue %s (%d with stale inputs) -> %s",
        len(preds),
        issue,
        stale,
        uri,
    )
    return {"issue_time_utc": str(issue), "predictions": len(preds), "stale": stale}

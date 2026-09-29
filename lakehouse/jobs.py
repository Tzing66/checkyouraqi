"""Lakehouse entry points for Airflow (no Airflow imports, runnable standalone)."""

from __future__ import annotations

import logging
from typing import Any

import boto3

from ingestion.settings import Settings
from lakehouse.export_public import export

log = logging.getLogger(__name__)


def export_public() -> dict[str, Any]:
    s = Settings()
    session = boto3.Session(profile_name=s.aws_profile, region_name=s.aws_region)
    manifest = export(session.client("athena"), session.client("s3"), s.data_bucket)
    log.info(
        "exported %d datasets at %s (%.2f MB scanned)",
        len(manifest["datasets"]),
        manifest["exported_at"],
        manifest["bytes_scanned"] / 1e6,
    )
    return {"exported_at": manifest["exported_at"], "datasets": len(manifest["datasets"])}

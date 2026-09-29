"""The production model: s3://<bucket>/models/production/ (owner decision 2026-09-29).

    models/production/manifest.json            {"version": ..., "horizons": {"24": {...}}, ...}
    models/production/<version>/lgbm_h24.txt    (+ .json sidecar with transform and features)

Promotion is manual for now (Phase 5 adds the automatic "beats production on the last 14 days"
rule):
    PYTHONPATH=. uv run python -m ml.registry promote --note "..."   # from data/ml/models
    PYTHONPATH=. uv run python -m ml.registry show
The version is the UTC time of promotion; older versions stay in S3 for rollback.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import lightgbm as lgb

from ingestion.settings import ROOT, Settings
from ml.features import HORIZONS

PREFIX = "models/production"
MANIFEST_KEY = f"{PREFIX}/manifest.json"
LOCAL_MODELS = ROOT / "data" / "ml" / "models"
CACHE = ROOT / "data" / "ml" / "production"


@dataclass(frozen=True)
class ProductionModel:
    version: str
    horizon_h: int
    booster: lgb.Booster
    target_transform: str
    features: list[str]


def s3_client(settings: Settings) -> Any:
    import boto3

    session = boto3.Session(profile_name=settings.aws_profile, region_name=settings.aws_region)
    return session.client("s3")


def promote(
    s3: Any,
    bucket: str,
    *,
    source: Path = LOCAL_MODELS,
    now: datetime | None = None,
    note: str = "",
) -> dict:
    version = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    manifest: dict[str, Any] = {"version": version, "note": note, "horizons": {}}
    for h in HORIZONS:
        model, meta = source / f"lgbm_h{h}.txt", source / f"lgbm_h{h}.json"
        if not model.exists() or not meta.exists():
            raise FileNotFoundError(f"missing {model.name} or {meta.name} in {source}")
        for f in (model, meta):
            s3.upload_file(str(f), bucket, f"{PREFIX}/{version}/{f.name}")
        manifest["horizons"][str(h)] = {
            "model_key": f"{PREFIX}/{version}/{model.name}",
            **json.loads(meta.read_text()),
        }
    card = source / "results.json"
    if card.exists():
        # Model card for the dashboard's Model health page, describing what's in production.
        body = {"version": version, "note": note, **json.loads(card.read_text())}
        s3.put_object(
            Bucket=bucket,
            Key=f"{PREFIX}/{version}/model_card.json",
            Body=json.dumps(body, indent=1).encode(),
            ContentType="application/json",
        )
        s3.put_object(
            Bucket=bucket,
            Key="public/model_card.json",
            Body=json.dumps(body, indent=1).encode(),
            ContentType="application/json",
        )
    # The manifest goes last: a half-finished promotion never becomes "production".
    s3.put_object(
        Bucket=bucket,
        Key=MANIFEST_KEY,
        Body=json.dumps(manifest, indent=1).encode(),
        ContentType="application/json",
    )
    return manifest


def load_production(s3: Any, bucket: str, *, cache: Path = CACHE) -> dict[int, ProductionModel]:
    manifest = json.loads(s3.get_object(Bucket=bucket, Key=MANIFEST_KEY)["Body"].read())
    version = manifest["version"]
    out = {}
    for h, spec in manifest["horizons"].items():
        local = cache / version / Path(spec["model_key"]).name
        if not local.exists():
            local.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(bucket, spec["model_key"], str(local))
        out[int(h)] = ProductionModel(
            version=version,
            horizon_h=int(h),
            booster=lgb.Booster(model_file=str(local)),
            target_transform=spec["target_transform"],
            features=spec["features"],
        )
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage the production model.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("promote", help="promote data/ml/models to production")
    p.add_argument("--note", default="")
    sub.add_parser("show", help="print the production manifest")
    args = parser.parse_args()
    s = Settings()
    s3 = s3_client(s)
    if args.cmd == "promote":
        m = promote(s3, s.data_bucket, note=args.note)
        print(f"promoted version {m['version']} ({', '.join(m['horizons'])}h)")
    else:
        print(s3.get_object(Bucket=s.data_bucket, Key=MANIFEST_KEY)["Body"].read().decode())


if __name__ == "__main__":
    main()

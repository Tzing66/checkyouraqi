"""Promotion rule (plan §7.8): promote a newly trained model only if it beats production on the
last 14 days.

Fairness: the full-data candidate has seen those 14 days, so it is NOT the one compared. A
challenger with identical settings is trained on data before the holdout (labels purged at the
boundary) and scored against the production model on the holdout. If the challenger wins on
mean MAE across horizons, the full-data models from `ml.train` (data/ml/models) are promoted.

    PYTHONPATH=. uv run python -m ml.promote            # after `python -m ml.train`
    PYTHONPATH=. uv run python -m ml.promote --dry-run  # compare only
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from dataclasses import dataclass

import pandas as pd

os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")

from ingestion.settings import Settings  # noqa: E402
from ml.data import load_features  # noqa: E402
from ml.evaluate import metrics  # noqa: E402
from ml.features import (  # noqa: E402
    HORIZONS,
    TARGET,
    decode_target,
    encode_target,
    level_base,
    to_model_frame,
)
from ml.registry import load_production, promote, s3_client  # noqa: E402
from ml.train import fit  # noqa: E402

log = logging.getLogger("ml.promote")

HOLDOUT_DAYS = 14
MIN_HOLDOUT_DAYS = 3  # less new data than this -> no decision, keep production


@dataclass(frozen=True)
class Comparison:
    holdout_start: pd.Timestamp
    challenger_mae: dict[int, float]
    production_mae: dict[int, float]
    no_decision_reason: str | None = None

    @property
    def challenger_mean(self) -> float:
        return sum(self.challenger_mae.values()) / len(self.challenger_mae)

    @property
    def production_mean(self) -> float:
        return sum(self.production_mae.values()) / len(self.production_mae)

    @property
    def promote(self) -> bool:
        return self.no_decision_reason is None and self.challenger_mean < self.production_mean

    def summary(self) -> str:
        if self.no_decision_reason:
            return f"no decision, keep production: {self.no_decision_reason}"
        per_h = ", ".join(
            f"{h}h {self.challenger_mae[h]:.2f} vs {self.production_mae[h]:.2f}"
            for h in sorted(self.challenger_mae)
        )
        verdict = "PROMOTE" if self.promote else "keep production"
        return (
            f"holdout from {self.holdout_start:%Y-%m-%d}: challenger mean MAE "
            f"{self.challenger_mean:.2f} vs production {self.production_mean:.2f} "
            f"({per_h}) -> {verdict}"
        )


def split_holdout(
    df: pd.DataFrame, days: int = HOLDOUT_DAYS, not_before: pd.Timestamp | None = None
) -> tuple[pd.Index, pd.Index, pd.Timestamp]:
    """(train_idx, holdout_idx, holdout_start). Holdout = prediction times in the last `days`
    days of available targets, but never before `not_before` (production's training cutoff, so
    production is also scored out-of-sample). Training rows' labels end before the holdout."""
    target_end = pd.to_datetime(df["target_hour_start_utc"]) + pd.Timedelta(hours=1)
    issue = pd.to_datetime(df["issue_time_utc"])
    holdout_start = target_end.max() - pd.Timedelta(days=days)
    if not_before is not None:
        holdout_start = max(holdout_start, pd.Timestamp(not_before))
    train_idx = df.index[target_end <= holdout_start]
    hold_idx = df.index[issue >= holdout_start]
    return train_idx, hold_idx, holdout_start


def compare(df: pd.DataFrame, production: dict) -> Comparison:
    cutoff = max(m.trained_until for m in production.values())
    train_idx, hold_idx, start = split_holdout(df, not_before=cutoff)
    new_days = (pd.to_datetime(df["target_hour_start_utc"]).max() - start) / pd.Timedelta(days=1)
    if new_days < MIN_HOLDOUT_DAYS:
        return Comparison(
            start,
            {},
            {},
            f"only {max(new_days, 0):.1f} day(s) of data after "
            f"production's training cutoff {cutoff:%Y-%m-%d %H:%M} UTC "
            f"(need {MIN_HOLDOUT_DAYS})",
        )
    challenger, current = {}, {}
    for h in HORIZONS:
        d = df[df["horizon_h"] == h]
        tr, ho = d.loc[d.index.intersection(train_idx)], d.loc[d.index.intersection(hold_idx)]
        if tr.empty or ho.empty:
            raise ValueError(f"not enough data for a {h}h holdout comparison")
        prod = production[h]
        model = fit(
            to_model_frame(tr), encode_target(tr[TARGET], level_base(tr), prod.target_transform)
        )
        x_ho = to_model_frame(ho)
        y_ho = ho[TARGET].astype("float64")
        challenger[h] = metrics(
            y_ho, decode_target(model.predict(x_ho), level_base(ho), prod.target_transform)
        )["mae"]
        current[h] = metrics(
            y_ho,
            decode_target(
                prod.booster.predict(x_ho[prod.features]), level_base(ho), prod.target_transform
            ),
        )["mae"]
    return Comparison(start, challenger, current)


def main() -> None:
    p = argparse.ArgumentParser(description="Champion/challenger promotion on a 14-day holdout.")
    p.add_argument("--dry-run", action="store_true", help="compare only, never promote")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    s = Settings()
    s3 = s3_client(s)
    df = load_features(settings=s)  # the cache written by `ml.train --refresh`
    result = compare(df, load_production(s3, s.data_bucket))
    log.info(result.summary())
    out = {"promoted": False, "summary": result.summary()}
    if result.promote and not args.dry_run:
        manifest = promote(s3, s.data_bucket, note=f"auto-promoted: {result.summary()}")
        out = {"promoted": True, "version": manifest["version"], "summary": result.summary()}
    print(json.dumps(out))
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a") as f:
            f.write(f"### Promotion\n\n{result.summary()}\n\npromoted: **{out['promoted']}**\n")


if __name__ == "__main__":
    main()

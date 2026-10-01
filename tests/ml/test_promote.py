import numpy as np
import pandas as pd

from ml.features import FEATURES, NUMERIC, to_model_frame
from ml.promote import Comparison, compare, split_holdout
from ml.registry import ProductionModel
from ml.train import fit


def _df(days=60, horizons=(24, 48, 72), seed=0):
    rng = np.random.default_rng(seed)
    issue = pd.date_range("2026-07-01 00:30", periods=days * 8, freq="3h")
    rows = []
    for h in horizons:
        for t in issue:
            r = {c: float(rng.uniform(10, 100)) for c in NUMERIC}
            r |= {
                "location_id": 235,
                "zone_id": "delhi_east",
                "horizon_h": h,
                "issue_time_utc": t,
                "target_hour_start_utc": t + pd.Timedelta(hours=h - 1),
            }
            r["target_pm25"] = r["pm25_lag0"] * 1.1 + rng.normal(0, 3)
            rows.append(r)
    return pd.DataFrame(rows)


def test_holdout_is_last_14_days_and_training_labels_end_before_it():
    df = _df()
    tr, ho, start = split_holdout(df)
    target_end = df["target_hour_start_utc"] + pd.Timedelta(hours=1)
    assert target_end.max() - start == pd.Timedelta(days=14)
    assert (target_end.loc[tr] <= start).all()
    assert (df.loc[ho, "issue_time_utc"] >= start).all()
    assert not set(tr) & set(ho)


def test_comparison_verdict():
    c = Comparison(pd.Timestamp("2026-09-01"), {24: 10.0, 48: 12.0}, {24: 11.0, 48: 12.5})
    assert c.promote and "PROMOTE" in c.summary()
    c2 = Comparison(pd.Timestamp("2026-09-01"), {24: 12.0}, {24: 11.0})
    assert not c2.promote and "keep production" in c2.summary()


def test_compare_scores_a_good_challenger_above_a_bad_production_model():
    df = _df()
    # "Production" = a model trained on noise, which the challenger should beat.
    noise = df.copy()
    noise["target_pm25"] = np.random.default_rng(1).uniform(0, 500, len(df))
    bad = fit(to_model_frame(noise), noise["target_pm25"]).booster_
    cutoff = df["target_hour_start_utc"].max() - pd.Timedelta(days=20)  # 20 days of new data
    production = {
        h: ProductionModel("v0", h, bad, "absolute", FEATURES, cutoff) for h in (24, 48, 72)
    }
    result = compare(df, production)
    assert set(result.challenger_mae) == {24, 48, 72}
    assert result.promote


def test_holdout_never_starts_before_production_cutoff():
    df = _df()
    cutoff = df["target_hour_start_utc"].max() - pd.Timedelta(days=5)
    tr, ho, start = split_holdout(df, not_before=cutoff)
    assert start == cutoff  # 5 days of new data, not 14 that production already saw


def test_no_decision_when_production_has_seen_all_the_data():
    df = _df()
    cutoff = df["target_hour_start_utc"].max() + pd.Timedelta(hours=1)  # trained on everything
    prod = {h: ProductionModel("v0", h, None, "absolute", FEATURES, cutoff) for h in (24, 48, 72)}
    result = compare(df, prod)
    assert not result.promote
    assert "no decision" in result.summary()

import numpy as np
import pandas as pd
import pytest

from ml import baselines
from ml.cv import monthly_folds
from ml.evaluate import category, metrics, skill
from ml.features import AUDIT_PREFIX, CATEGORICAL, FEATURES, KEYS, NUMERIC, TARGET, to_model_frame

# --- leakage guards on the Python side ----------------------------------------------------


def test_features_never_include_target_audit_or_timing_keys():
    assert TARGET not in FEATURES
    assert not [f for f in FEATURES if f.startswith(AUDIT_PREFIX)]
    timing_keys = [k for k in KEYS if k not in CATEGORICAL]
    assert not set(timing_keys) & set(FEATURES)
    assert "target_pm25" not in NUMERIC


def test_to_model_frame_types_and_rejects_missing():
    row = {c: 1.0 for c in NUMERIC} | {"location_id": 235, "zone_id": "delhi_east"}
    x = to_model_frame(pd.DataFrame([row, row]))
    assert list(x.columns) == FEATURES
    assert str(x["zone_id"].dtype) == "category"
    assert x["pm25_lag0"].dtype == np.float32
    with pytest.raises(KeyError):
        to_model_frame(pd.DataFrame([{"location_id": 1}]))


def _frame(start="2025-02-01", months=9, horizon=72):
    issue = pd.date_range(start, periods=months * 30 * 8, freq="3h")
    return pd.DataFrame(
        {
            "issue_time_utc": issue,
            "target_hour_start_utc": issue + pd.Timedelta(hours=horizon - 1),
        }
    )


def test_folds_are_monthly_forward_and_purged():
    df = _frame()
    folds = list(monthly_folds(df, min_train_months=6))
    assert [f.name for f in folds] == ["2025-08", "2025-09", "2025-10"]
    for f in folds:
        train, test = df.loc[f.train_idx], df.loc[f.test_idx]
        # every training label ends before the first test prediction time
        assert (train["target_hour_start_utc"] + pd.Timedelta(hours=1)).max() <= f.test_start
        assert test["issue_time_utc"].min() >= f.test_start
        assert test["issue_time_utc"].max() < f.test_end
        assert not set(f.train_idx) & set(f.test_idx)


def test_purge_drops_issue_times_whose_labels_cross_the_boundary():
    df = _frame(horizon=72)
    fold = next(monthly_folds(df, min_train_months=6))
    last_train_issue = df.loc[fold.train_idx, "issue_time_utc"].max()
    assert last_train_issue <= fold.test_start - pd.Timedelta(hours=72)


# --- metrics and baselines ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, "good"),
        (30, "good"),
        (30.4, "good"),
        (30.6, "satisfactory"),
        (60, "satisfactory"),
        (91, "poor"),
        (250, "very_poor"),
        (251, "severe"),
        (900, "severe"),
    ],
)
def test_category_uses_rounded_cpcb_breakpoints(value, expected):
    assert category([value])[0] == expected


def test_metrics_and_skill():
    m = metrics(pd.Series([10.0, 100.0]), pd.Series([20.0, 100.0]))
    assert m["mae"] == 5.0
    assert m["rmse"] == pytest.approx(np.sqrt(50))
    assert m["bias"] == 5.0
    assert m["category_accuracy"] == 1.0  # 10 and 20 are both "good"
    assert skill(8.0, 10.0) == pytest.approx(0.2)


def test_baselines_fall_back_to_persistence():
    df = pd.DataFrame(
        {
            "pm25_last_valid": [100.0, 50.0],
            "pm25_same_hour_7d": [90.0, None],
            "cams_pm25_lagged": [None, 70.0],
        }
    )
    assert baselines.predict(df, "persistence").tolist() == [100.0, 50.0]
    assert baselines.predict(df, "seasonal_naive").tolist() == [90.0, 50.0]
    assert baselines.predict(df, "cams_lagged").tolist() == [100.0, 70.0]
    with pytest.raises(ValueError):
        baselines.predict(df, "nope")


def test_target_transform_round_trips_and_extrapolates():
    from ml.features import decode_target, encode_target, level_base

    df = pd.DataFrame({"pm25_mean_24h": [20.0, None, 300.0], "pm25_last_valid": [25.0, 40.0, 0.2]})
    base = level_base(df)
    assert base.tolist() == [20.0, 40.0, 300.0]
    y = pd.Series([30.0, 10.0, 450.0])
    for mode in ("absolute", "log_ratio"):
        back = decode_target(encode_target(y, base, mode), base, mode)
        assert back.round(6).tolist() == y.tolist()
    # a ratio learned at low levels scales to high ones: +50% at 20 -> 30, at 300 -> 450
    ratio = encode_target(pd.Series([30.0]), pd.Series([20.0]), "log_ratio").iloc[0]
    assert decode_target([ratio], pd.Series([300.0]), "log_ratio").iloc[0] == pytest.approx(
        np.expm1(ratio + np.log1p(300.0))
    )
    with pytest.raises(ValueError):
        encode_target(y, base, "nope")

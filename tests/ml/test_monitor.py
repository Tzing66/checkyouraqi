import numpy as np
import pandas as pd

from ml.features import NUMERIC
from ml.monitor import (
    _drifted,
    drift_report,
    served_features_key,
    served_features_payload,
    summarise_drift,
)


def test_drift_direction_depends_on_method():
    assert _drifted("ValueDrift(column=a,method=K-S p_value,threshold=0.05)", 0.01)
    assert not _drifted("ValueDrift(column=a,method=K-S p_value,threshold=0.05)", 0.5)
    assert _drifted("ValueDrift(column=a,method=Wasserstein distance (normed),threshold=0.1)", 0.3)
    assert not _drifted(
        "ValueDrift(column=a,method=Wasserstein distance (normed),threshold=0.1)", 0.05
    )


def test_summarise_drift_parses_snapshot():
    snap = {
        "metrics": [
            {
                "metric_name": "DriftedColumnsCount(drift_share=0.5)",
                "value": {"count": 1, "share": 0.5},
            },
            {"metric_name": "ValueDrift(column=a,method=K-S p_value,threshold=0.05)", "value": 0.2},
            {
                "metric_name": "ValueDrift(column=b,method=K-S p_value,threshold=0.05)",
                "value": 0.001,
            },
        ]
    }
    s = summarise_drift(snap)
    assert s["share_drifted"] == 0.5
    assert s["drifted_columns"] == ["b"]
    assert s["columns"]["a"]["drifted"] is False


def test_drift_report_detects_a_shifted_feature():
    rng = np.random.default_rng(0)
    ref = pd.DataFrame({c: rng.normal(50, 10, 800) for c in NUMERIC})
    cur = pd.DataFrame({c: rng.normal(50, 10, 300) for c in NUMERIC})
    cur["pm25_lag0"] = rng.normal(150, 10, 300)  # e.g. stale/season shift
    html, summary = drift_report(ref, cur)
    assert "<html" in html.lower()
    assert "pm25_lag0" in summary["drifted_columns"]
    assert summary["share_drifted"] < 0.5


def test_served_features_payload_is_json_safe():
    df = pd.DataFrame(
        {
            "issue_time_utc": pd.to_datetime(["2026-09-29 10:30"]),
            "x": [1.5],
            "is_stale_input": [True],
        }
    )
    rows = served_features_payload(df)
    assert rows == [{"issue_time_utc": "2026-09-29T10:30:00Z", "x": 1.5, "is_stale_input": True}]
    assert served_features_key(pd.Timestamp("2026-09-29 10:30")) == (
        "bronze/served_features/dt=2026-09-29/hour=10/features.json.gz"
    )

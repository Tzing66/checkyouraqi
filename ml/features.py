"""Feature and column definitions for the forecasting models.

Everything here comes from aqi_gold.fct_features_hourly, where each feature is built only from
data that existed at prediction time (dbt test assert_features_no_leakage). This module decides
which of those columns the model may see: never the target, the audit columns or the timing
keys (tests/ml/test_features.py).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TARGET = "target_pm25"
HORIZONS = (24, 48, 72)

KEYS = [
    "location_id",
    "zone_id",
    "city_id",
    "horizon_h",
    "issue_hour_start_utc",
    "issue_time_utc",
    "target_hour_start_utc",
]
AUDIT_PREFIX = "audit_"

CATEGORICAL = ["location_id", "zone_id"]

NUMERIC = [
    # station history at prediction time
    "pm25_lag0", "pm25_lag1", "pm25_lag3", "pm25_lag6", "pm25_lag12", "pm25_lag24",
    "pm25_lag48", "pm25_mean_6h", "pm25_max_6h", "pm25_mean_24h", "pm25_max_24h",
    "pm25_24h", "valid_hours_24h", "pm25_same_hour_7d", "pm25_last_valid",
    "hours_since_last_valid",
    "zone_pm25_median", "city_pm25_median", "city_pm25_24h_median",
    # weather observed at prediction time
    "obs_temperature_2m", "obs_relative_humidity_2m", "obs_wind_speed_10m",
    "obs_wind_direction_10m", "obs_boundary_layer_height",
    # weather at the target hour as forecast >= (h/24 + 1) days earlier
    "fc_temperature_2m", "fc_relative_humidity_2m", "fc_wind_speed_10m",
    "fc_wind_direction_10m", "fc_precipitation", "fc_surface_pressure",
    # regional background and fires
    "cams_pm25_lagged",
    "fires_upwind_24h", "fires_upwind_48h", "fires_upwind_72h", "fires_upwind_frp_24h",
    "fires_nw_arc_24h", "fires_nw_arc_72h",
    # calendar at the target hour (known in advance)
    "target_hour_ist", "target_dow_ist", "target_month",
    "target_is_festival", "target_is_weekend",
]  # fmt: skip

FEATURES = CATEGORICAL + NUMERIC


def to_model_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Select and type the model inputs (categoricals as pandas category, rest float32)."""
    missing = [c for c in FEATURES if c not in df.columns]
    if missing:
        raise KeyError(f"feature columns missing from data: {missing}")
    x = df[FEATURES].copy()
    for c in CATEGORICAL:
        x[c] = x[c].astype("string").astype("category")
    for c in NUMERIC:
        x[c] = pd.to_numeric(x[c], errors="coerce").astype("float32")
    return x


# --- target transform -------------------------------------------------------------------------
# Trees can't predict beyond the target range they were trained on, so an absolute-PM2.5 model
# trained before the first pollution season can't forecast it. "log_ratio" instead models the
# change relative to the station's recent level: log1p(target) - log1p(base), base = 24h mean
# at prediction time. That extrapolates to any pollution level ("tomorrow ~ 1.2x today").

TARGET_TRANSFORMS = ("absolute", "log_ratio")


def level_base(df: pd.DataFrame) -> pd.Series:
    base = df["pm25_mean_24h"].astype("float64").fillna(df["pm25_last_valid"].astype("float64"))
    return base.clip(lower=1.0)


def encode_target(y: pd.Series, base: pd.Series, mode: str) -> pd.Series:
    if mode == "absolute":
        return y.astype("float64")
    if mode == "log_ratio":
        return np.log1p(y.astype("float64").clip(lower=0)) - np.log1p(base)
    raise ValueError(f"unknown target transform {mode!r}")


def decode_target(pred, base: pd.Series, mode: str) -> pd.Series:
    pred = pd.Series(np.asarray(pred, dtype="float64"), index=base.index)
    if mode == "absolute":
        return pred
    if mode == "log_ratio":
        return np.expm1(pred + np.log1p(base)).clip(lower=0)
    raise ValueError(f"unknown target transform {mode!r}")

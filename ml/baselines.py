"""Baselines every model is reported against (plan §7).

- persistence: the station's last valid PM2.5 at prediction time.
- seasonal_naive: mean of the same hour over the previous 7 days (for horizons that are whole
  days, "same hour yesterday" is identical to persistence, so the plan's intent needs a week).
- cams_lagged: CAMS model PM2.5 for the zone, 12h before prediction time. Historical CAMS
  *forecasts* don't exist (Open-Meteo keeps none), so the true CAMS-forecast baseline is scored
  from 2026-09-28 on, from forecasts we collect live (Phase 4).

Each falls back to persistence where its own input is missing, so every row gets a prediction.
"""

from __future__ import annotations

import pandas as pd

BASELINES = ("persistence", "seasonal_naive", "cams_lagged")


def predict(df: pd.DataFrame, name: str) -> pd.Series:
    persistence = df["pm25_last_valid"].astype("float64")
    if name == "persistence":
        return persistence
    if name == "seasonal_naive":
        return df["pm25_same_hour_7d"].astype("float64").fillna(persistence)
    if name == "cams_lagged":
        return df["cams_pm25_lagged"].astype("float64").fillna(persistence)
    raise ValueError(f"unknown baseline {name!r}")

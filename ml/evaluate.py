"""Forecast metrics: MAE, RMSE, AQI category accuracy, and skill against a baseline."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from ingestion.settings import CONFIG_DIR


def load_breakpoints(path: Path = CONFIG_DIR / "aqi_breakpoints.yaml") -> list[tuple[str, float]]:
    """[(category, upper_bound)] in order; the last category is open-ended (inf)."""
    cats = yaml.safe_load(path.read_text())["categories"]
    return [(c["name"], float(c["concentration"][1] or np.inf)) for c in cats]


BREAKPOINTS = load_breakpoints()


def category(values: pd.Series | np.ndarray) -> np.ndarray:
    """India AQI PM2.5 category of each value, after CPCB-style rounding to whole µg/m³.

    Indicative when applied to hourly values: the official category uses the 24h average.
    """
    v = np.round(np.asarray(values, dtype="float64"))
    uppers = np.array([u for _, u in BREAKPOINTS])
    names = np.array([n for n, _ in BREAKPOINTS])
    idx = np.searchsorted(uppers, v, side="left")
    return names[np.clip(idx, 0, len(names) - 1)]


def metrics(y_true: pd.Series, y_pred: pd.Series) -> dict[str, float]:
    y_true = np.asarray(y_true, dtype="float64")
    y_pred = np.asarray(y_pred, dtype="float64")
    err = y_pred - y_true
    return {
        "mae": float(np.mean(np.abs(err))),
        "rmse": float(np.sqrt(np.mean(err**2))),
        "bias": float(np.mean(err)),
        "category_accuracy": float(np.mean(category(y_true) == category(y_pred))),
        "n": int(len(y_true)),
    }


def skill(model_mae: float, baseline_mae: float) -> float:
    """1 - MAE_model / MAE_baseline: > 0 means the model beats the baseline."""
    return 1.0 - model_mae / baseline_mae

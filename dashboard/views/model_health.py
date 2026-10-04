"""Forecast accuracy: plain-language summary first (typical error per horizon, vs simply
assuming no change), then the monthly backtest, what the model relies on, the full baseline
table, and live monitoring."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard.charts import feature_bars, monthly_mae_chart
from dashboard.state import footer, get_snapshot, theme_mode
from dashboard.style import card, note

snap = get_snapshot()
model = snap.files.get("model_card")
st.title("How accurate are the forecasts?")
if not model:
    note("No model has been published yet.", kind="info")
    st.stop()

months = len(model["horizons"]["24"].get("per_month", []))
st.markdown(
    f'<div class="cya-sub">Tested month by month on {months} months of past data, each time '
    "with a model that had never seen that month.</div>",
    unsafe_allow_html=True,
)

# --- summary ------------------------------------------------------------------------------
cols = st.columns(3, gap="medium")
labels = {"24": "Tomorrow", "48": "In 2 days", "72": "In 3 days"}
for col, h in zip(cols, ("24", "48", "72"), strict=True):
    spec = model["horizons"][h]
    skill = spec["skill"].get("persistence")
    better = (
        f"{skill:.0%} better than assuming no change"
        if skill is not None and skill > 0
        else "no better than assuming no change"
    )
    with col:
        card(
            f'<div class="cya-label">{labels[h]} ({h}h ahead)</div>'
            f'<div class="cya-value-sm">±{spec["model"]["mae"]:.0f}<small>µg/m³</small></div>'
            f'<div class="cya-meta">typical error · {better}</div>'
        )
st.caption(
    "Typical error = average gap between forecast and measurement (MAE). For scale, the "
    "“Satisfactory” band is 31–60 µg/m³."
)

# --- by month + what it relies on ---------------------------------------------------------
st.subheader("Error by month")
h = st.segmented_control(
    "Forecast horizon",
    ["24", "48", "72"],
    default="24",
    format_func=lambda x: f"{x}h ahead",
    label_visibility="collapsed",
)
spec = model["horizons"][h or "24"]
left, right = st.columns([3, 2], gap="large")
with left, st.container(border=True):
    st.altair_chart(monthly_mae_chart(spec["per_month"], theme_mode()), width="stretch")
with right, st.container(border=True):
    st.markdown("**What the model relies on most**")
    st.altair_chart(feature_bars(spec["top_features"], theme_mode()), width="stretch")
st.caption(
    "Errors peak in Oct–Nov, the stubble-burning and Diwali season. The data holds only one "
    "such season so far (station history starts Feb 2025)."
)

with st.expander("Full comparison with simple baselines"):
    names = {
        "persistence": "Assume no change (last value)",
        "seasonal_naive": "Same hour, average of last 7 days",
        "cams_lagged": "CAMS pollution model",
    }
    rows = [
        {
            "Predictor": "This model (LightGBM)",
            "MAE": spec["model"]["mae"],
            "RMSE": spec["model"]["rmse"],
            "Bias": spec["model"]["bias"],
            "Category accuracy": spec["model"]["category_accuracy"],
            "Model is better by": None,
        }
    ]
    for k, b in spec["baselines"].items():
        rows.append(
            {
                "Predictor": names.get(k, k),
                "MAE": b["mae"],
                "RMSE": b["rmse"],
                "Bias": b["bias"],
                "Category accuracy": b["category_accuracy"],
                "Model is better by": spec["skill"][k],
            }
        )
    st.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        width="stretch",
        column_config={
            "MAE": st.column_config.NumberColumn(format="%.1f µg/m³"),
            "RMSE": st.column_config.NumberColumn(format="%.1f"),
            "Bias": st.column_config.NumberColumn(format="%+.1f"),
            "Category accuracy": st.column_config.NumberColumn(format="percent"),
            "Model is better by": st.column_config.NumberColumn(
                format="percent", help="1 − MAE(model) / MAE(baseline); positive = model wins"
            ),
        },
    )
    st.caption(
        f"Model {model['version']} · trained on {model['trained_rows']:,} rows · "
        f"{model['validation']}. Category accuracy uses hourly values (indicative)."
    )

# --- live monitoring ----------------------------------------------------------------------
st.subheader("Live checks")
acc = snap.files.get("accuracy_summary") or {}
drift = snap.files.get("drift_summary") or {}
a_col, d_col = st.columns(2, gap="medium")
with a_col:
    live = (acc.get("horizons") or {}).get(h or "24", {}).get("last_7d")
    if acc.get("has_live_accuracy") and live:
        card(
            '<div class="cya-label">Live error, last 7 days</div>'
            f'<div class="cya-value-sm">±{live["mae"]:.0f}<small>µg/m³</small></div>'
            f'<div class="cya-meta">{live["n"]} forecasts checked against measurements</div>'
        )
    else:
        card(
            '<div class="cya-label">Live error</div>'
            '<div class="cya-advice">Fills in once new measurements arrive for hours we '
            "forecast. The data source has been paused, so there's nothing to check yet."
            "</div>"
        )
with d_col:
    if drift.get("status") == "ok":
        share = drift.get("share_drifted", 0)
        drifted = ", ".join(drift.get("drifted_columns", [])) or "none"
        card(
            '<div class="cya-label">Input drift vs training data</div>'
            f'<div class="cya-value-sm">{share:.0%}<small>of inputs</small></div>'
            f'<div class="cya-meta">Drifted: {drifted}</div>'
        )
    else:
        status = str(drift.get("status", "not run yet"))
        detail = (
            "Not enough recent forecasts to compare with the training data yet."
            if status.startswith("insufficient")
            else f"Check status: {status}."
        )
        card(f'<div class="cya-label">Input drift</div><div class="cya-advice">{detail}</div>')

footer(snap)

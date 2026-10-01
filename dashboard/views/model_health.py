"""Model health: backtest vs baselines, accuracy by month, top features, live accuracy, drift."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard.charts import feature_bars, monthly_mae_chart
from dashboard.state import footer, get_snapshot, theme_mode

snap = get_snapshot()
card = snap.files.get("model_card")
st.title("Model health")

if not card:
    st.error("No model card published yet (promote a model with `python -m ml.registry`).")
    st.stop()

st.caption(
    f"Production model **{card['version']}** · {card.get('note', '')} · trained on "
    f"{card['trained_rows']:,} rows · {card['validation']}"
)

h = st.segmented_control("Horizon", ["24", "48", "72"], default="24", format_func=lambda x: f"{x}h")
spec = card["horizons"][h]

# --- backtest vs baselines ----------------------------------------------------------------
st.subheader(f"Backtest: {h}h ahead")
names = {
    "persistence": "Persistence (last value)",
    "seasonal_naive": "Seasonal naive (same hour, last 7 days)",
    "cams_lagged": "CAMS model PM2.5 (12h lag)",
}
rows = [
    {
        "Predictor": "LightGBM (this model)",
        "MAE": spec["model"]["mae"],
        "RMSE": spec["model"]["rmse"],
        "Bias": spec["model"]["bias"],
        "Category accuracy": spec["model"]["category_accuracy"],
        "Model skill vs it": None,
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
            "Model skill vs it": spec["skill"][k],
        }
    )
table = pd.DataFrame(rows)
st.dataframe(
    table,
    hide_index=True,
    width="stretch",
    column_config={
        "MAE": st.column_config.NumberColumn(format="%.1f µg/m³"),
        "RMSE": st.column_config.NumberColumn(format="%.1f"),
        "Bias": st.column_config.NumberColumn(format="%+.1f"),
        "Category accuracy": st.column_config.NumberColumn(format="percent"),
        "Model skill vs it": st.column_config.NumberColumn(
            format="percent", help="1 − MAE(model) / MAE(baseline); positive = model is better"
        ),
    },
)
st.caption(
    "Walk-forward validation: each month is predicted by a model trained only on "
    "earlier data. Category accuracy uses hourly values (indicative)."
)

c1, c2 = st.columns([3, 2], gap="large")
with c1:
    st.markdown("**MAE by test month**")
    st.altair_chart(monthly_mae_chart(spec["per_month"], theme_mode()), width="stretch")
    st.caption(
        "The model struggles in Oct–Nov 2025: its first pollution season, with no earlier "
        "season in the training data (OpenAQ history starts Feb 2025)."
    )
with c2:
    st.markdown("**What the model relies on** (share of gain)")
    st.altair_chart(feature_bars(spec["top_features"], theme_mode()), width="stretch")

# --- live monitoring ----------------------------------------------------------------------
st.subheader("Live accuracy")
acc = snap.files.get("accuracy_summary") or {}
if not acc.get("has_live_accuracy"):
    st.info(
        "No live accuracy yet: it needs forecasts whose target hours have since been "
        "measured. The air-quality source has not sent new readings since the forecasts "
        "started, so this fills in automatically once it recovers."
    )
else:
    live = acc["horizons"].get(h, {})
    cols = st.columns(2)
    for col, window in zip(cols, ("last_7d", "last_30d"), strict=True):
        m = live.get(window)
        with col:
            if m:
                st.metric(
                    f"MAE, {window.replace('last_', 'last ')}",
                    f"{m['mae']:.1f} µg/m³",
                    help=f"{m['n']} forecasts; bias {m['bias']:+.1f}",
                )
            else:
                st.metric(f"MAE, {window.replace('last_', 'last ')}", "—")

st.subheader("Input drift")
drift = snap.files.get("drift_summary") or {}
if drift.get("status") == "ok":
    share = drift.get("share_drifted", 0)
    st.metric("Features drifted vs training data", f"{share:.0%}")
    st.caption("Drifted: " + (", ".join(drift.get("drifted_columns", [])) or "none"))
else:
    st.info(f"Drift check: {drift.get('status', 'not run yet')}.")

footer(snap)

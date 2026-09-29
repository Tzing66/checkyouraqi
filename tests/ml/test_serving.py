import json
from datetime import UTC, datetime
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
import pytest

from ml.features import FEATURES, NUMERIC, to_model_frame
from ml.predict import predict_frame, prediction_key, to_payload
from ml.registry import MANIFEST_KEY, ProductionModel, load_production, promote


def _features(n=6, horizons=(24, 48)):
    rng = np.random.default_rng(0)
    rows = []
    for h in horizons:
        for i in range(n):
            r = {c: float(rng.uniform(10, 200)) for c in NUMERIC}
            r |= {
                "location_id": 235 + i,
                "zone_id": "delhi_east",
                "horizon_h": h,
                "issue_time_utc": pd.Timestamp("2026-09-29 11:30"),
                "target_hour_start_utc": pd.Timestamp("2026-09-29 10:30") + pd.Timedelta(hours=h),
                "input_age_hours": 120,
                "is_stale_input": True,
                "last_valid_hour_utc": pd.Timestamp("2026-09-24 17:30"),
            }
            rows.append(r)
    return pd.DataFrame(rows)


def _booster(df):
    x = to_model_frame(df)
    return lgb.LGBMRegressor(n_estimators=5, verbose=-1).fit(x, df["pm25_lag0"]).booster_


def test_predict_frame_uses_each_horizons_model_and_keeps_stale_labels():
    df = _features()
    b = _booster(df)
    models = {h: ProductionModel("v1", h, b, "absolute", FEATURES) for h in (24, 48)}
    preds = predict_frame(df, models)
    assert len(preds) == 12
    assert set(preds["horizon_h"]) == {24, 48}
    assert (preds["pm25_pred"] >= 0).all()
    assert preds["is_stale_input"].all()
    assert (preds["input_age_hours"] == 120).all()
    assert (preds["model_version"] == "v1").all()


def test_payload_is_json_serialisable_and_keyed_by_issue_hour():
    df = _features(n=1, horizons=(24,))
    b = _booster(_features())
    preds = predict_frame(df, {24: ProductionModel("v1", 24, b, "absolute", FEATURES)})
    payload = to_payload(preds, datetime(2026, 9, 29, 11, 40, tzinfo=UTC))
    json.dumps(payload)
    p = payload["predictions"][0]
    assert p["issue_time_utc"] == "2026-09-29T11:30:00Z"
    assert p["target_hour_start_utc"] == "2026-09-30T10:30:00Z"
    assert p["last_valid_hour_utc"] == "2026-09-24T17:30:00Z"
    assert payload["predicted_at"] == "2026-09-29T11:40:00Z"
    assert prediction_key(pd.Timestamp("2026-09-29 11:30")) == (
        "bronze/predictions/dt=2026-09-29/hour=11/predictions.json.gz"
    )


class FakeS3:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.order: list[str] = []

    def upload_file(self, path, bucket, key):
        self.objects[key] = Path(path).read_bytes()
        self.order.append(key)

    def put_object(self, Bucket, Key, Body, **kw):
        self.objects[Key] = Body
        self.order.append(Key)

    def get_object(self, Bucket, Key):
        import io

        return {"Body": io.BytesIO(self.objects[Key])}

    def download_file(self, bucket, key, path):
        Path(path).write_bytes(self.objects[key])


def test_promote_then_load_round_trip(tmp_path):
    src = tmp_path / "models"
    src.mkdir()
    b = _booster(_features())
    for h in (24, 48, 72):
        b.save_model(str(src / f"lgbm_h{h}.txt"))
        (src / f"lgbm_h{h}.json").write_text(
            json.dumps({"horizon_h": h, "target_transform": "absolute", "features": FEATURES})
        )
    (src / "results.json").write_text(json.dumps({"horizons": {}}))
    s3 = FakeS3()
    m = promote(s3, "bkt", source=src, now=datetime(2026, 9, 29, 12, tzinfo=UTC), note="t")
    assert m["version"] == "20260929T120000Z"
    assert s3.order[-1] == MANIFEST_KEY  # manifest strictly last
    assert "public/model_card.json" in s3.objects
    loaded = load_production(s3, "bkt", cache=tmp_path / "cache")
    assert set(loaded) == {24, 48, 72}
    assert loaded[48].target_transform == "absolute"
    x = to_model_frame(_features(n=2, horizons=(48,)))
    assert len(loaded[48].booster.predict(x)) == 2


def test_promote_refuses_incomplete_model_set(tmp_path):
    with pytest.raises(FileNotFoundError):
        promote(FakeS3(), "bkt", source=tmp_path)

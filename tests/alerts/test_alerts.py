import io
import json
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pandas as pd
import pytest

from alerts import jobs, telegram
from ingestion.freshness import FeedReport, FeedStatus
from ingestion.settings import Settings

NOW = datetime(2026, 10, 4, 15, 0, tzinfo=UTC)
TOKEN = "123456:AAHsecretsecretsecretsecretsecret"


def settings(**kw) -> Settings:
    base = {"telegram_bot_token": TOKEN, "telegram_alert_chat_id": "42", "data_bucket": "b"}
    return Settings(_env_file=None, **{**base, **kw})


def report(status: FeedStatus, share: float) -> FeedReport:
    return FeedReport(status, share, datetime(2026, 10, 2, 8, 30, tzinfo=UTC))


# --- telegram.send --------------------------------------------------------------------------


def test_send_posts_to_chat():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    assert telegram.send("hello", settings=settings(), http=http)
    assert seen["url"].endswith(f"/bot{TOKEN}/sendMessage")
    assert seen["body"]["chat_id"] == "42" and seen["body"]["text"] == "hello"


@pytest.mark.parametrize("token", ["", "CHANGE_ME"])
def test_send_is_off_without_real_keys(token):
    http = httpx.Client(transport=httpx.MockTransport(lambda r: pytest.fail("no request")))
    assert not telegram.send("x", settings=settings(telegram_bot_token=token), http=http)


def test_send_failure_never_raises_or_logs_the_token(caplog):
    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with caplog.at_level(logging.WARNING):
        assert not telegram.send("x", settings=settings(), http=http)
    assert TOKEN not in caplog.text


# --- task failures --------------------------------------------------------------------------


def test_redact_hides_keys_in_exception_text():
    text = "500 for url https://firms.modaps.eosdis.nasa.gov/api/area/csv/MAPKEY12345/VIIRS"
    assert "MAPKEY12345" not in jobs.redact(text, ["MAPKEY12345", ""])


def test_failure_message_is_short_and_specific():
    msg = jobs.failure_message("ingest_openaq", "extract", "scheduled__x", 3, ValueError("boom"))
    assert msg.startswith("❌ ingest_openaq.extract failed")
    assert "ValueError: boom" in msg and "attempt 3" in msg


def test_repeat_alerts_are_muted_for_six_hours():
    assert jobs.should_alert(None, NOW)
    assert not jobs.should_alert((NOW - timedelta(hours=5)).isoformat(), NOW)
    assert jobs.should_alert((NOW - timedelta(hours=6)).isoformat(), NOW)


class FakeS3:
    class exceptions:
        class NoSuchKey(Exception):
            pass

    def __init__(self, objects=None):
        self.objects = dict(objects or {})

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise self.exceptions.NoSuchKey()
        return {"Body": io.BytesIO(self.objects[Key])}

    def put_object(self, Bucket, Key, Body, ContentType):
        self.objects[Key] = Body


def test_task_failed_sends_once_then_mutes(monkeypatch):
    s3, sent = FakeS3(), []
    monkeypatch.setattr(jobs, "Settings", lambda: settings())
    monkeypatch.setattr(jobs, "_s3", lambda s: s3)
    monkeypatch.setattr(jobs, "send", lambda text, settings: sent.append(text) or True)
    ti = SimpleNamespace(dag_id="predict", task_id="predict", run_id="r1", try_number=2)
    jobs.task_failed({"ti": ti, "exception": RuntimeError("x")})
    jobs.task_failed({"ti": ti, "exception": RuntimeError("x")})
    assert len(sent) == 1
    assert "predict.predict" in json.loads(s3.objects[jobs.TASK_STATE_KEY])


def test_task_failed_never_raises(monkeypatch):
    monkeypatch.setattr(jobs, "Settings", lambda: settings())
    monkeypatch.setattr(jobs, "_s3", lambda s: (_ for _ in ()).throw(RuntimeError("no aws")))
    jobs.task_failed({"ti": SimpleNamespace(dag_id="d", task_id="t")})  # no exception


# --- data-source status ---------------------------------------------------------------------


def test_feed_messages_only_on_change():
    outage = report(FeedStatus.OUTAGE, 1.0)
    assert "🔴 OpenAQ outage: 100%" in jobs.feed_change_message(None, outage)
    assert "2 Oct, 14:00 IST" in jobs.feed_change_message(None, outage)
    assert jobs.feed_change_message("outage", outage) is None
    assert jobs.feed_change_message(None, report(FeedStatus.OK, 0.0)) is None
    assert "🟢" in jobs.feed_change_message("outage", report(FeedStatus.OK, 0.1))
    assert "🟠" in jobs.feed_change_message("ok", report(FeedStatus.DEGRADED, 0.4))


def _snapshot_s3(last_reading) -> FakeS3:
    buf = io.BytesIO()
    pd.DataFrame({"location_id": [1, 2], "last_pm25_reading_utc": [last_reading] * 2}).to_parquet(
        buf
    )
    manifest = {"datasets": {"station_status": {"keys": ["public/station_status/p0"]}}}
    return FakeS3(
        {
            "public/manifest.json": json.dumps(manifest).encode(),
            "public/station_status/p0": buf.getvalue(),
        }
    )


def test_feed_check_announces_outage_once_then_recovery(monkeypatch):
    s3, sent = _snapshot_s3(pd.Timestamp("2026-10-02 08:30", tz="UTC")), []
    monkeypatch.setattr(jobs, "Settings", lambda: settings())
    monkeypatch.setattr(jobs, "_s3", lambda s: s3)
    monkeypatch.setattr(jobs, "send", lambda text, settings: sent.append(text) or True)

    assert jobs.feed_status_hourly(NOW) == {"status": "outage", "previous": None, "sent": True}
    assert jobs.feed_status_hourly(NOW)["sent"] is False  # unchanged: silent
    s3.objects.update(_snapshot_s3(pd.Timestamp(NOW) - pd.Timedelta(minutes=30)).objects)
    assert jobs.feed_status_hourly(NOW) == {"status": "ok", "previous": "outage", "sent": True}
    assert [m[0] for m in sent] == ["🔴", "🟢"]


def test_feed_check_retries_when_send_fails(monkeypatch):
    s3 = _snapshot_s3(pd.Timestamp("2026-10-02 08:30", tz="UTC"))
    monkeypatch.setattr(jobs, "Settings", lambda: settings())
    monkeypatch.setattr(jobs, "_s3", lambda s: s3)
    monkeypatch.setattr(jobs, "send", lambda text, settings: False)
    jobs.feed_status_hourly(NOW)
    assert jobs.FEED_STATE_KEY not in s3.objects  # nothing recorded: next hour tries again

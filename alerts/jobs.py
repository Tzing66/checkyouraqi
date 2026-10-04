"""Pipeline alerts to the owner's private Telegram chat.

- Task failures: an Airflow on_failure_callback (fires after the task's last retry). The same
  task alerts at most once per REPEAT_AFTER, so an hourly task failing all day sends a few
  messages, not 24.
- Data-source status: hourly, after the public export, the feed status the dashboard shows
  (ok / degraded / outage, same function) is compared with the last one sent; only changes are
  announced. Nothing here alerts on PM2.5 levels.

Alert state is two small JSON files in the data bucket (one per writer, so they never race).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from alerts.telegram import configured, send
from ingestion.freshness import FeedReport, FeedStatus, format_ist
from ingestion.settings import Settings

log = logging.getLogger(__name__)

TASK_STATE_KEY = "ops/alerts/tasks.json"
FEED_STATE_KEY = "ops/alerts/feed.json"
REPEAT_AFTER = timedelta(hours=6)


class AlertState:
    def __init__(self, s3: Any, bucket: str, key: str) -> None:
        self._s3, self.bucket, self.key = s3, bucket, key

    def load(self) -> dict:
        try:
            return json.loads(self._s3.get_object(Bucket=self.bucket, Key=self.key)["Body"].read())
        except self._s3.exceptions.NoSuchKey:
            return {}

    def save(self, data: dict) -> None:
        self._s3.put_object(
            Bucket=self.bucket,
            Key=self.key,
            Body=json.dumps(data, indent=1).encode(),
            ContentType="application/json",
        )


def _s3(s: Settings):
    import boto3

    return boto3.Session(profile_name=s.aws_profile, region_name=s.aws_region).client("s3")


# --- task failures --------------------------------------------------------------------------


def redact(text: str, secrets: list[str]) -> str:
    """Exception texts can carry request URLs, and FIRMS puts its key in the URL path."""
    for secret in secrets:
        if secret and len(secret) >= 8:
            text = text.replace(secret, "***")
    return text


def failure_message(
    dag_id: str, task_id: str, run_id: str, try_number: int | None, exc: BaseException | None
) -> str:
    lines = [f"❌ {dag_id}.{task_id} failed", f"run {run_id}, attempt {try_number or '?'}"]
    if exc is not None:
        lines.append(f"{type(exc).__name__}: {str(exc)[:400]}")
    lines.append(f"(repeats of this task are muted for {REPEAT_AFTER.seconds // 3600}h)")
    return "\n".join(lines)


def should_alert(last_sent: str | None, now: datetime) -> bool:
    return last_sent is None or now - datetime.fromisoformat(last_sent) >= REPEAT_AFTER


def task_failed(context: dict) -> None:
    """Airflow on_failure_callback. Never raises (it would hide the task's own error)."""
    try:
        ti = context["ti"]
        s = Settings()
        if not configured(s):
            return
        now = datetime.now(UTC)
        key = f"{ti.dag_id}.{ti.task_id}"
        state = AlertState(_s3(s), s.data_bucket, TASK_STATE_KEY)
        data = state.load()
        if not should_alert(data.get(key), now):
            return
        text = failure_message(
            ti.dag_id,
            ti.task_id,
            getattr(ti, "run_id", "?"),
            getattr(ti, "try_number", None),
            context.get("exception"),
        )
        text = redact(text, [s.openaq_api_key, s.firms_map_key, s.telegram_bot_token])
        if send(text, settings=s):
            data[key] = now.isoformat()
            state.save(data)
    except Exception as e:
        log.warning("failure alert not sent: %s", type(e).__name__)


# --- data-source status ---------------------------------------------------------------------


def feed_change_message(previous: str | None, report: FeedReport) -> str | None:
    """Message for a status change, or None. The first check only speaks if not ok."""
    current = report.status.value
    if current == previous or (previous is None and report.status == FeedStatus.OK):
        return None
    pct = round(report.share_not_live * 100)
    newest = format_ist(report.newest_reading_utc) if report.newest_reading_utc else "none"
    if report.status == FeedStatus.OUTAGE:
        return (
            f"🔴 OpenAQ outage: {pct}% of stations have no live reading "
            f"(newest reading {newest}). The dashboard shows last-known data."
        )
    if report.status == FeedStatus.DEGRADED:
        return f"🟠 OpenAQ degraded: {pct}% of stations not live (newest reading {newest})."
    return f"🟢 OpenAQ back to normal: {100 - pct}% of stations live (newest reading {newest})."


def current_feed(source: Any, now: datetime) -> FeedReport:
    """The feed status from the public snapshot, exactly as the dashboard and API compute it."""
    from dashboard.data import MANIFEST_KEY, _read_parquet
    from dashboard.ui import feed_state

    manifest = json.loads(source.get(MANIFEST_KEY))
    keys = manifest["datasets"]["station_status"]["keys"]
    status = _read_parquet([source.get(k) for k in keys])
    last = dict(zip(status["location_id"], status["last_pm25_reading_utc"], strict=True))
    report, _ = feed_state(last, now)
    return report


def feed_status_hourly(now: datetime | None = None) -> dict:
    from dashboard.data import S3Source

    now = now or datetime.now(UTC)
    s = Settings()
    s3 = _s3(s)
    report = current_feed(S3Source(s.data_bucket, s3), now)
    state = AlertState(s3, s.data_bucket, FEED_STATE_KEY)
    data = state.load()
    previous = data.get("status")
    message = feed_change_message(previous, report)
    sent = bool(message) and send(message, settings=s)
    # Record the new status once announced (or when nothing needed announcing); a failed send
    # leaves the old status, so the next hour tries again.
    if message is None or sent:
        state.save({"status": report.status.value, "checked_at": now.isoformat()})
    result = {"status": report.status.value, "previous": previous, "sent": sent}
    log.info("feed status check: %s", result)
    return result

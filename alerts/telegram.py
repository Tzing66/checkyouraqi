"""Send a message to the owner's private Telegram chat (pipeline and data-source alerts).

Never raises: an alert that fails to send must not fail, or hide the error of, the task that
triggered it. Unset keys (local dev, or the SSM "CHANGE_ME" placeholder) mean "alerts off".
"""

from __future__ import annotations

import logging

import httpx

from ingestion.settings import Settings

log = logging.getLogger(__name__)

API = "https://api.telegram.org"
PLACEHOLDER = "CHANGE_ME"
MAX_LEN = 4000  # Telegram's limit is 4096 characters


def configured(s: Settings) -> bool:
    return all(v and v != PLACEHOLDER for v in (s.telegram_bot_token, s.telegram_alert_chat_id))


def send(text: str, *, settings: Settings | None = None, http: httpx.Client | None = None) -> bool:
    s = settings or Settings()
    if not configured(s):
        log.info("telegram alerts not configured; not sent: %s", text[:200])
        return False
    try:
        client = http or httpx.Client(timeout=15)
        resp = client.post(
            f"{API}/bot{s.telegram_bot_token}/sendMessage",
            json={
                "chat_id": s.telegram_alert_chat_id,
                "text": text[:MAX_LEN],
                "disable_web_page_preview": True,
            },
        )
        resp.raise_for_status()
        return True
    except Exception as e:  # never let alerting break the pipeline
        # The error text can contain the request URL, which embeds the bot token.
        log.warning("telegram send failed: %s", type(e).__name__)
        return False

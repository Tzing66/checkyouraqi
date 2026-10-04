"""Runtime settings, read from the environment or the repo's .env file."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    openaq_api_key: str = ""
    firms_map_key: str = ""
    data_bucket: str = ""
    aws_profile: str | None = None
    aws_region: str = "ap-south-1"
    telegram_bot_token: str = ""
    telegram_alert_chat_id: str = ""

"""Application configuration loaded from environment variables."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY: str = os.environ.get("SECRET_KEY", "dev-only-change-me")

    # Database
    DATABASE_PATH: str = os.environ.get(
        "DATABASE_PATH", str(Path(__file__).parent.parent / "config.db")
    )

    # Webhook
    PERRY_WEBHOOK_SECRET: str = os.environ.get("PERRY_WEBHOOK_SECRET", "")

    # Network
    BIND_HOST: str = os.environ.get("BIND_HOST", "0.0.0.0")
    BIND_PORT: int = int(os.environ.get("BIND_PORT", "8080"))

    # RS-485 defaults (web UI can override at runtime; these are fallbacks)
    RS485_PORT: str = os.environ.get("RS485_PORT", "/dev/ttyUSB0")
    RS485_BAUD: int = int(os.environ.get("RS485_BAUD", "9600"))
    CLOCK_ADDRESS: int = int(os.environ.get("CLOCK_ADDRESS", "0x01"), 0)

    # Behavior
    DEFAULT_COUNTDOWN_SECONDS: int = int(
        os.environ.get("DEFAULT_COUNTDOWN_SECONDS", "1800")
    )

    # Flask
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    WTF_CSRF_TIME_LIMIT = None  # No expiry while logged in

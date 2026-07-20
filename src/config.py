"""Application settings, logging, and shared date formatting."""

from __future__ import annotations

import logging
import logging.config
import math
import os
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", str(PROJECT_ROOT / "data" / "app.db")))
UPSTREAM_BASE_URL = os.getenv("UPSTREAM_BASE_URL", "http://127.0.0.1:8001").rstrip("/")
CANDIDATE_ID = os.getenv("CANDIDATE_ID", "local-candidate").strip()
MAX_ARCHIVE_BYTES = 1_000_000
MAX_FILE_BYTES = 2_000
BATCH_SIZE = 3
DIGITS = "0123456789"
NSK = ZoneInfo("Asia/Novosibirsk")

logging.config.dictConfig(
    {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "console": {
                "format": "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            }
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "console",
            }
        },
        "root": {
            "handlers": ["console"],
            "level": os.getenv("LOG_LEVEL", "INFO").upper(),
        },
    }
)
logger = logging.getLogger("file_catalog")


def positive_int_env(name: str, default: int) -> int:
    """Read a strictly positive integer setting or fail with a useful message."""
    raw_value = os.getenv(name, str(default))
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer, got {raw_value!r}") from exc
    if value < 1:
        raise RuntimeError(f"{name} must be greater than zero, got {value}")
    return value


def positive_float_env(name: str, default: float) -> float:
    """Read a finite positive floating-point setting."""
    raw_value = os.getenv(name, str(default))
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number, got {raw_value!r}") from exc
    if not math.isfinite(value) or value <= 0:
        raise RuntimeError(f"{name} must be a finite number greater than zero, got {raw_value!r}")
    return value


def non_negative_float_env(name: str, default: float) -> float:
    """Read a finite non-negative floating-point setting."""
    raw_value = os.getenv(name, str(default))
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number, got {raw_value!r}") from exc
    if not math.isfinite(value) or value < 0:
        raise RuntimeError(f"{name} must be a finite non-negative number, got {raw_value!r}")
    return value


REQUEST_TIMEOUT_SECONDS = positive_float_env("REQUEST_TIMEOUT_SECONDS", 30)
REQUEST_INTERVAL_SECONDS = non_negative_float_env("REQUEST_INTERVAL_SECONDS", 3)
DEFAULT_RETRY_SECONDS = positive_int_env("DEFAULT_RETRY_SECONDS", 10)
MAX_TRANSIENT_RETRIES = positive_int_env("MAX_TRANSIENT_RETRIES", 5)

if not CANDIDATE_ID:
    raise RuntimeError("CANDIDATE_ID must not be empty")
if not UPSTREAM_BASE_URL:
    raise RuntimeError("UPSTREAM_BASE_URL must not be empty")


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def format_nsk(value: str | None) -> str:
    if not value:
        return "—"
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError:
        logger.warning("Invalid timestamp in the database: %r", value)
        return "—"
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(NSK).strftime("%d.%m.%Y %H:%M:%S НСК")

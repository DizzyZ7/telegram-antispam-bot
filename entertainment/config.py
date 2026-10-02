"""Entertainment configuration and scoped defaults."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

LOGGER = logging.getLogger(__name__)

DEFAULT_LAZINESS = 92
DEFAULT_COOLDOWN_SECONDS = 45


def _env_positive_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return int(default)
    try:
        value = int(raw.strip())
    except (TypeError, ValueError):
        LOGGER.warning("Ignoring invalid integer value for %s", name)
        return int(default)
    if value <= 0:
        LOGGER.warning("Ignoring non-positive value for %s", name)
        return int(default)
    return value


MEMORY_LIMIT = _env_positive_int("ENTERTAINMENT_MEMORY_LIMIT", 100_000)
GENERATION_SAMPLE_LIMIT = min(
    _env_positive_int("ENTERTAINMENT_GENERATION_SAMPLE_LIMIT", 1_500),
    MEMORY_LIMIT,
)
MEMORY_PRUNE_BUFFER = _env_positive_int("ENTERTAINMENT_MEMORY_PRUNE_BUFFER", 1_000)
BOOTSTRAP_TEXT_EVENT_THRESHOLD = _env_positive_int(
    "ENTERTAINMENT_BOOTSTRAP_TEXT_EVENT_THRESHOLD",
    10_000,
)
MEDIA_REPEAT_COOLDOWN_SECONDS = _env_positive_int(
    "ENTERTAINMENT_MEDIA_REPEAT_COOLDOWN_SECONDS",
    21_600,
)
MIN_MESSAGES_TO_GENERATE = 25
MIN_MESSAGE_LENGTH = 3
MAX_MESSAGE_LENGTH = 600
MAX_GENERATED_TOKENS = 30

URL_RE = re.compile(r"(?:https?://|www\.|t\.me/)", re.IGNORECASE)
CHAT_ID_SPLIT_RE = re.compile(r"[\s,;]+")


def parse_chat_ids(raw_value: str | None) -> frozenset[int]:
    """Parse comma/space/semicolon separated Telegram chat ids."""
    if not raw_value:
        return frozenset()

    result: set[int] = set()
    for item in CHAT_ID_SPLIT_RE.split(raw_value.strip()):
        if not item:
            continue
        try:
            result.add(int(item))
        except ValueError:
            LOGGER.warning("Ignoring invalid ENTERTAINMENT_CHAT_IDS item: %r", item)
    return frozenset(result)


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().casefold() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class EntertainmentDatabaseConfig:
    database_url: str | None
    sqlite_path: Path
    allow_sqlite_fallback: bool = False

    @classmethod
    def from_env(cls, data_dir: Path) -> "EntertainmentDatabaseConfig":
        database_url = (os.getenv("DATABASE_URL") or "").strip() or None
        return cls(
            database_url=database_url,
            sqlite_path=Path(data_dir) / "entertainment.db",
            allow_sqlite_fallback=_env_flag("ENTERTAINMENT_DB_FALLBACK_SQLITE"),
        )


ENTERTAINMENT_CHAT_IDS = parse_chat_ids(os.getenv("ENTERTAINMENT_CHAT_IDS"))

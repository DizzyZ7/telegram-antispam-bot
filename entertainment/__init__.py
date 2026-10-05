"""Scoped entertainment package with compatibility exports for v1 callers."""

from .config import (
    BOOTSTRAP_TEXT_EVENT_THRESHOLD,
    DEFAULT_COOLDOWN_SECONDS,
    DEFAULT_LAZINESS,
    ENTERTAINMENT_CHAT_IDS,
    MEMORY_LIMIT,
    parse_chat_ids,
)
from .moderation_safety_service import EntertainmentService
from .generation import generate_chat_text
from .models import BehaviorMode, EntertainmentSettings, normalize_topic_id
from .router import EntertainmentChatFilter, register_entertainment_handlers
from .scheduler import EntertainmentSupervisor
from .storage import LegacyCompatibleEntertainmentStorage, SQLiteEntertainmentStorage

# Transitional public alias: main.py and the original tests still construct
# EntertainmentStorage(Path). Internally the service already depends on the
# new protocol in entertainment.storage.base.
EntertainmentStorage = LegacyCompatibleEntertainmentStorage

__all__ = [
    "BOOTSTRAP_TEXT_EVENT_THRESHOLD",
    "BehaviorMode",
    "DEFAULT_COOLDOWN_SECONDS",
    "DEFAULT_LAZINESS",
    "ENTERTAINMENT_CHAT_IDS",
    "EntertainmentChatFilter",
    "EntertainmentService",
    "EntertainmentSettings",
    "EntertainmentStorage",
    "EntertainmentSupervisor",
    "SQLiteEntertainmentStorage",
    "MEMORY_LIMIT",
    "generate_chat_text",
    "normalize_topic_id",
    "parse_chat_ids",
    "register_entertainment_handlers",
]

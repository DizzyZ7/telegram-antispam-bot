"""Scoped entertainment package with compatibility exports for v1 callers."""

from .config import (
    DEFAULT_COOLDOWN_SECONDS,
    DEFAULT_LAZINESS,
    ENTERTAINMENT_CHAT_IDS,
    MEMORY_LIMIT,
    parse_chat_ids,
)
from .generation import generate_chat_text
from .models import EntertainmentSettings, normalize_topic_id
from .router import EntertainmentChatFilter, register_entertainment_handlers
from .service import EntertainmentService, EntertainmentStorage

__all__ = [
    "DEFAULT_COOLDOWN_SECONDS",
    "DEFAULT_LAZINESS",
    "ENTERTAINMENT_CHAT_IDS",
    "EntertainmentChatFilter",
    "EntertainmentService",
    "EntertainmentSettings",
    "EntertainmentStorage",
    "MEMORY_LIMIT",
    "generate_chat_text",
    "normalize_topic_id",
    "parse_chat_ids",
    "register_entertainment_handlers",
]

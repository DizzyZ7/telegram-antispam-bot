"""Shared entertainment data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .config import DEFAULT_COOLDOWN_SECONDS, DEFAULT_LAZINESS


class BehaviorMode(str, Enum):
    """User-facing autonomous participation mode."""

    CALM = "calm"
    ALIVE = "alive"
    ACTIVE = "active"

    @property
    def display_name(self) -> str:
        return {
            BehaviorMode.CALM: "Спокойный",
            BehaviorMode.ALIVE: "Живой",
            BehaviorMode.ACTIVE: "Активный",
        }[self]


def normalize_behavior_mode(value: object) -> BehaviorMode:
    """Return a safe behavior mode for persisted/untrusted values."""
    if isinstance(value, BehaviorMode):
        return value
    try:
        return BehaviorMode(str(value).strip().casefold())
    except (TypeError, ValueError):
        return BehaviorMode.ALIVE


class MemoryEventType(str, Enum):
    """Supported human Telegram event kinds stored by Culture Memory."""

    TEXT = "text"
    EMOJI = "emoji"
    STICKER = "sticker"
    PHOTO = "photo"
    ANIMATION = "animation"


@dataclass(frozen=True, slots=True)
class MemoryEvent:
    """One canonical, topic-isolated Culture Memory event."""

    id: int | None
    chat_id: int
    topic_id: int
    message_id: int | None
    user_id: int
    event_type: MemoryEventType
    created_at: int
    text: str | None = None
    caption: str | None = None
    reply_to_message_id: int | None = None
    file_id: str | None = None
    file_unique_id: str | None = None
    sticker_emoji: str | None = None
    sticker_set_name: str | None = None
    media_width: int | None = None
    media_height: int | None = None
    media_duration: int | None = None
    is_forwarded: bool = False
    legacy_source_id: int | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MemoryCounts:
    """Compact per-topic Culture Memory counters for the /fun panel."""

    total: int = 0
    text: int = 0
    emoji: int = 0
    sticker: int = 0
    photo: int = 0
    animation: int = 0


class EntertainmentActionType(str, Enum):
    CONTEXTUAL_REPLY = "contextual_reply"
    REMIXED_PHRASE = "remixed_phrase"
    MEMORY_CALLBACK = "memory_callback"


@dataclass(frozen=True, slots=True)
class EntertainmentActionRecord:
    id: int | None
    chat_id: int
    topic_id: int
    action_type: EntertainmentActionType
    trigger_message_id: int | None
    created_at: int
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EntertainmentSettings:
    enabled: bool = True
    behavior_mode: BehaviorMode = BehaviorMode.ALIVE
    quiet_hours_start: int | None = None
    quiet_hours_end: int | None = None
    timezone: str = "Europe/Moscow"
    autonomous_text_enabled: bool = True

    # V1 compatibility only. These fields remain while old database rows,
    # commands and one-release migration paths still exist. Autonomy v2 decision
    # code must not use them for action selection.
    laziness: int = DEFAULT_LAZINESS
    cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS

    @property
    def spontaneous_chance_percent(self) -> int:
        """Deprecated V1 compatibility property."""
        return max(0, min(100, 100 - self.laziness))


def normalize_topic_id(message_thread_id: int | None) -> int:
    """Normalize Telegram's absent/general topic marker to zero."""
    return int(message_thread_id or 0)

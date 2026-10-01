"""Shared entertainment data models."""

from __future__ import annotations

from dataclasses import dataclass
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
    try:
        return BehaviorMode(str(value))
    except (TypeError, ValueError):
        return BehaviorMode.ALIVE


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

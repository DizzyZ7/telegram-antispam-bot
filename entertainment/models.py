"""Shared entertainment data models."""

from __future__ import annotations

from dataclasses import dataclass

from .config import DEFAULT_COOLDOWN_SECONDS, DEFAULT_LAZINESS


@dataclass(frozen=True, slots=True)
class EntertainmentSettings:
    enabled: bool = True
    laziness: int = DEFAULT_LAZINESS
    cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS

    @property
    def spontaneous_chance_percent(self) -> int:
        return max(0, min(100, 100 - self.laziness))


def normalize_topic_id(message_thread_id: int | None) -> int:
    """Normalize Telegram's absent/general topic marker to zero."""
    return int(message_thread_id or 0)

"""Topic-local activity context used by the autonomy engine."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ActivitySnapshot:
    chat_id: int
    topic_id: int
    messages_1m: int
    messages_5m: int
    messages_previous_5m: int
    messages_15m: int
    active_users_5m: int
    seconds_since_human: float | None


__all__ = ["ActivitySnapshot"]

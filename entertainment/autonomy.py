"""Pure, deterministic autonomy decisions derived from topic activity."""

from __future__ import annotations

from enum import Enum

from .context import ActivitySnapshot


class ConversationPhase(str, Enum):
    QUIET = "quiet"
    WARMING_UP = "warming_up"
    ACTIVE = "active"
    PEAK = "peak"
    COOLDOWN = "cooldown"


def derive_phase(snapshot: ActivitySnapshot) -> ConversationPhase:
    """Classify one forum topic without consulting global chat activity."""
    if snapshot.messages_5m == 0 and snapshot.messages_15m <= 1:
        return ConversationPhase.QUIET
    if 1 <= snapshot.messages_5m <= 4 and snapshot.messages_previous_5m >= 8:
        return ConversationPhase.COOLDOWN
    if snapshot.messages_5m >= 12 and snapshot.active_users_5m >= 3:
        return ConversationPhase.PEAK
    if snapshot.messages_5m >= 5:
        return ConversationPhase.ACTIVE
    return ConversationPhase.WARMING_UP


__all__ = ["ConversationPhase", "derive_phase"]

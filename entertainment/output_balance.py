"""Balanced output realization for an ALREADY authorized Entertainment action.

Culture Memory media is a valuable part of Fosgen's humor, but formerly it
won every time a matching sticker or photo existed, even if Generation v3 had
produced a good text. This pure gate reserves more opportunities for text
without increasing action frequency or changing any safety/topic permissions.
"""
from __future__ import annotations

import random
from collections.abc import Sequence

from .models import EntertainmentActionRecord, EntertainmentActionType, MemoryEvent, MemoryEventType
from .personality import detect_literary_topic

_OUTPUT_WINDOW = 10
_MEDIA_SHARE_CEILING = 0.45
_MEDIA_PROBABILITY = 0.32
_DIRECT_MEDIA_PROBABILITY = 0.18
_RECOVERY_CONTEXT_SECONDS = 20 * 60
_RECOVERY_EVENT_LIMIT = 24
_TEXT_TYPES = frozenset({MemoryEventType.TEXT, MemoryEventType.EMOJI, MemoryEventType.PHOTO})
_ACTION_TYPES = frozenset({
    EntertainmentActionType.CONTEXTUAL_REPLY,
    EntertainmentActionType.REMIXED_PHRASE,
    EntertainmentActionType.MEMORY_CALLBACK,
})


def should_send_media(
    recent_actions: Sequence[EntertainmentActionRecord],
    *,
    text_ready: bool,
    direct_reply: bool,
    rng: random.Random,
) -> bool:
    """Cap media's share WHEN safe text also exists, never ban media outright.

    History is already scoped to one chat/topic in the storage query. All
    decisions occur after the normal Autonomy/Presence approval: only the
    payload changes, not the permitted count of bot messages.
    """
    if not text_ready:
        return True  # Avoid turning approved media into silence without text.

    observations = [
        action for action in recent_actions
        if action.action_type in _ACTION_TYPES
    ][:_OUTPUT_WINDOW]
    if observations:
        # Newer action first. Avoid two successive media messages if text is ready.
        if observations[0].action_type is EntertainmentActionType.MEMORY_CALLBACK:
            return False
        media_count = sum(
            action.action_type is EntertainmentActionType.MEMORY_CALLBACK
            for action in observations
        )
        if (media_count + 1) / (len(observations) + 1) > _MEDIA_SHARE_CEILING:
            return False

    probability = (
        _DIRECT_MEDIA_PROBABILITY if direct_reply else _MEDIA_PROBABILITY
    )
    return rng.random() < probability


def recent_literary_context(
    recent_events: Sequence[MemoryEvent],
    *,
    now: int,
) -> str | None:
    """Find a *fresh human* literary topic, not an old caption/bot's phrase.

    No cross-topic data: callers pass only an already-scoped snapshot. Do not
    copy this text into the output, use it only to choose a safe original joke.
    """
    for event in reversed(recent_events[-_RECOVERY_EVENT_LIMIT:]):
        if event.sender_is_bot or event.is_forwarded or event.is_command:
            continue
        if event.event_type not in _TEXT_TYPES:
            continue
        age = int(now) - int(event.created_at)
        if age < 0 or age > _RECOVERY_CONTEXT_SECONDS:
            continue
        raw = event.text if event.event_type in {MemoryEventType.TEXT, MemoryEventType.EMOJI} else event.caption
        if isinstance(raw, str) and detect_literary_topic(raw) is not None:
            return raw
    return None


__all__ = ["should_send_media", "recent_literary_context"]

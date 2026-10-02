"""Chronological Culture Memory shaping for local text generation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from collections.abc import Sequence

from .models import MemoryEvent, MemoryEventType

DEFAULT_RUN_GAP_SECONDS = 8 * 60
_RECENT_SOURCE_WEIGHT = 3
_HISTORICAL_SOURCE_WEIGHT = 1
_ADJACENT_TURN_BONUS = 1
_REPLY_TURN_BONUS = 3
_CONTEXT_LIMIT = 50

# Broad Unicode ranges are intentionally local and dependency-free. This is
# only candidate extraction; Task 3 owns final emoji ranking/style behavior.
_EMOJI_RE = re.compile(
    "["
    "\U0001F1E6-\U0001F1FF"
    "\U0001F300-\U0001FAFF"
    "\u2600-\u27BF"
    "]",
    re.UNICODE,
)


@dataclass(frozen=True, slots=True)
class CultureGenerationContext:
    """Bounded generation material derived from one chat/topic."""

    source_messages: list[str]
    context_messages: list[str]
    emoji_candidates: list[str] = field(default_factory=list)
    recent_event_count: int = 0
    historical_event_count: int = 0
    conversation_run_count: int = 0


def _event_order_key(event: MemoryEvent) -> tuple[int, int, int]:
    return (
        int(event.created_at),
        int(event.message_id) if event.message_id is not None else -1,
        int(event.id) if event.id is not None else -1,
    )


def _event_text(event: MemoryEvent) -> str | None:
    value: str | None
    if event.event_type in {MemoryEventType.TEXT, MemoryEventType.EMOJI}:
        value = event.text
    else:
        value = event.caption
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split()).strip()
    return cleaned or None


def _emoji_from_event(event: MemoryEvent) -> list[str]:
    values: list[str] = []
    text = _event_text(event)
    if text:
        values.extend(_EMOJI_RE.findall(text))
    if event.sticker_emoji:
        values.extend(_EMOJI_RE.findall(event.sticker_emoji))
    return values


def build_conversation_runs(
    events: Sequence[MemoryEvent],
    *,
    gap_seconds: int = DEFAULT_RUN_GAP_SECONDS,
) -> list[list[MemoryEvent]]:
    """Group chronological events into bounded conversation runs."""

    if not events:
        return []
    gap = max(1, int(gap_seconds))
    ordered = sorted(events, key=_event_order_key)
    runs: list[list[MemoryEvent]] = []
    current: list[MemoryEvent] = []
    previous: MemoryEvent | None = None

    for event in ordered:
        if previous is not None:
            same_scope = (
                int(event.chat_id) == int(previous.chat_id)
                and int(event.topic_id) == int(previous.topic_id)
            )
            too_far = int(event.created_at) - int(previous.created_at) > gap
            if not same_scope or too_far:
                if current:
                    runs.append(current)
                current = []
        current.append(event)
        previous = event

    if current:
        runs.append(current)
    return runs


def _append_weighted(target: list[str], text: str, weight: int) -> None:
    target.extend([text] * max(1, int(weight)))


def _add_run_relations(
    run: Sequence[MemoryEvent],
    source_messages: list[str],
    *,
    base_weight: int,
) -> None:
    """Add phrase/turn relations without crossing author token boundaries."""

    by_message_id = {
        int(event.message_id): event
        for event in run
        if event.message_id is not None
    }

    for event in run:
        text = _event_text(event)
        if text:
            _append_weighted(source_messages, text, base_weight)

    for previous, current in zip(run, run[1:]):
        previous_text = _event_text(previous)
        current_text = _event_text(current)
        if not previous_text or not current_text:
            continue

        if int(previous.user_id) == int(current.user_id):
            # Telegram users often split one sentence/thought into successive
            # messages. Joining is allowed only within the same author's turn.
            joined = f"{previous_text} {current_text}".strip()
            _append_weighted(source_messages, joined, max(1, base_weight - 1))
        else:
            # Cross-user chronology is a turn association, never token fusion.
            _append_weighted(source_messages, current_text, _ADJACENT_TURN_BONUS)

    for event in run:
        if event.reply_to_message_id is None:
            continue
        target = by_message_id.get(int(event.reply_to_message_id))
        if target is None or int(target.topic_id) != int(event.topic_id):
            continue
        response_text = _event_text(event)
        target_text = _event_text(target)
        if response_text and target_text:
            _append_weighted(source_messages, response_text, _REPLY_TURN_BONUS)


def build_culture_context(
    recent_events: Sequence[MemoryEvent],
    historical_windows: Sequence[Sequence[MemoryEvent]],
    *,
    trigger_text: str | None = None,
) -> CultureGenerationContext:
    """Build weighted local-generation input while preserving turn semantics."""

    recent = sorted(recent_events, key=_event_order_key)
    historical = [
        sorted(window, key=_event_order_key)
        for window in historical_windows
        if window
    ]

    source_messages: list[str] = []
    recent_runs = build_conversation_runs(recent)
    for run in recent_runs:
        _add_run_relations(run, source_messages, base_weight=_RECENT_SOURCE_WEIGHT)

    historical_count = 0
    for window in historical:
        historical_count += len(window)
        for run in build_conversation_runs(window):
            _add_run_relations(run, source_messages, base_weight=_HISTORICAL_SOURCE_WEIGHT)

    context_messages = [
        text
        for event in recent
        if (text := _event_text(event)) is not None
    ][-_CONTEXT_LIMIT:]

    cleaned_trigger = " ".join(trigger_text.split()).strip() if isinstance(trigger_text, str) else ""
    if cleaned_trigger:
        # Deliberate duplicate weight: Generation v2 consumes context as a flat
        # list, so repeating the current trigger makes it the strongest anchor.
        context_messages.extend([cleaned_trigger, cleaned_trigger])

    emoji_candidates: list[str] = []
    # Recent culture is intentionally stronger than sampled history.
    for event in recent:
        emoji_candidates.extend(_emoji_from_event(event) * 3)
    for window in historical:
        for event in window:
            emoji_candidates.extend(_emoji_from_event(event))

    return CultureGenerationContext(
        source_messages=source_messages,
        context_messages=context_messages,
        emoji_candidates=emoji_candidates,
        recent_event_count=len(recent),
        historical_event_count=historical_count,
        conversation_run_count=len(recent_runs),
    )


__all__ = [
    "CultureGenerationContext",
    "DEFAULT_RUN_GAP_SECONDS",
    "build_conversation_runs",
    "build_culture_context",
]

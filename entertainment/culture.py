"""Chronological Culture Memory shaping for local text generation."""

from __future__ import annotations

import hashlib
import random
import re
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

from writers_moderation import contains_prohibited_language

from .models import MemoryCounts, MemoryEvent, MemoryEventType

DEFAULT_RUN_GAP_SECONDS = 8 * 60
_RECENT_SOURCE_WEIGHT = 10
_HISTORICAL_SOURCE_WEIGHT = 3
_ADJACENT_TURN_BONUS = 1
_REPLY_TURN_BONUS = 3
_CONTEXT_LIMIT = 50
_EMOJI_STYLE_CHANCE = 0.55
_POST_BOOTSTRAP_SPECIAL_PROBABILITY = 0.40

_EMOJI_RE = re.compile(
    "["
    "\U0001F1E6-\U0001F1FF"
    "\U0001F300-\U0001FAFF"
    "\u2600-\u27BF"
    "]",
    re.UNICODE,
)
_WORD_RE = re.compile(r"[^\W\d_]{2,}", re.UNICODE)


@dataclass(frozen=True, slots=True)
class CultureGenerationContext:
    """Bounded generation material derived from one chat/topic."""

    source_messages: list[str]
    context_messages: list[str]
    emoji_candidates: list[str] = field(default_factory=list)
    emoji_term_scores: dict[str, dict[str, int]] = field(default_factory=dict)
    recent_event_count: int = 0
    historical_event_count: int = 0
    conversation_run_count: int = 0


@dataclass(frozen=True, slots=True)
class CultureMemorySnapshot:
    """One bounded read shared by text, emoji and media realization."""

    recent_events: list[MemoryEvent]
    historical_windows: list[list[MemoryEvent]]
    counts: MemoryCounts
    generation: CultureGenerationContext


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
    if not cleaned or contains_prohibited_language(cleaned):
        return None
    return cleaned


def _emoji_from_event(event: MemoryEvent) -> list[str]:
    values: list[str] = []
    text = _event_text(event)
    if text:
        values.extend(_EMOJI_RE.findall(text))
    if event.sticker_emoji:
        values.extend(_EMOJI_RE.findall(event.sticker_emoji))
    return values


def _terms(text: str | None) -> set[str]:
    if not text:
        return set()
    return {match.casefold() for match in _WORD_RE.findall(text)}


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


def _is_special_source(event: MemoryEvent) -> bool:
    return bool(event.sender_is_bot) or bool(event.is_command)


def _sample_value(event: MemoryEvent, *, weight_seed: int) -> float:
    message_key = (
        int(event.message_id)
        if event.message_id is not None
        else int(event.id) if event.id is not None else -1
    )
    payload = (
        f"{int(weight_seed)}:{int(event.chat_id)}:{int(event.topic_id)}:"
        f"{message_key}:{int(event.user_id)}:{event.event_type.value}"
    ).encode("utf-8")
    digest = hashlib.blake2b(payload, digest_size=8).digest()
    return int.from_bytes(digest, "big") / float(1 << 64)


def _include_source(
    event: MemoryEvent,
    *,
    mature_corpus: bool,
    weight_seed: int,
) -> bool:
    if not mature_corpus or not _is_special_source(event):
        return True
    return _sample_value(event, weight_seed=weight_seed) < _POST_BOOTSTRAP_SPECIAL_PROBABILITY


def _add_run_relations(
    run: Sequence[MemoryEvent],
    source_messages: list[str],
    *,
    base_weight: int,
    mature_corpus: bool,
    weight_seed: int,
) -> None:
    """Add phrase/turn relations without creating adjacency across sampled-out events."""

    by_message_id = {
        int(event.message_id): event
        for event in run
        if event.message_id is not None
    }

    for event in run:
        if not _include_source(event, mature_corpus=mature_corpus, weight_seed=weight_seed):
            continue
        text = _event_text(event)
        if text:
            _append_weighted(source_messages, text, base_weight)

    for previous, current in zip(run, run[1:]):
        if not _include_source(previous, mature_corpus=mature_corpus, weight_seed=weight_seed):
            continue
        if not _include_source(current, mature_corpus=mature_corpus, weight_seed=weight_seed):
            continue
        previous_text = _event_text(previous)
        current_text = _event_text(current)
        if not previous_text or not current_text:
            continue

        if int(previous.user_id) == int(current.user_id):
            joined = f"{previous_text} {current_text}".strip()
            _append_weighted(source_messages, joined, max(1, base_weight - 1))
        else:
            _append_weighted(source_messages, current_text, _ADJACENT_TURN_BONUS)

    for event in run:
        if event.reply_to_message_id is None:
            continue
        if not _include_source(event, mature_corpus=mature_corpus, weight_seed=weight_seed):
            continue
        target = by_message_id.get(int(event.reply_to_message_id))
        if target is None or int(target.topic_id) != int(event.topic_id):
            continue
        if not _include_source(target, mature_corpus=mature_corpus, weight_seed=weight_seed):
            continue
        response_text = _event_text(event)
        target_text = _event_text(target)
        if response_text and target_text:
            _append_weighted(source_messages, response_text, _REPLY_TURN_BONUS)


def _scope_from_inputs(
    recent_events: Sequence[MemoryEvent],
    historical_windows: Sequence[Sequence[MemoryEvent]],
) -> tuple[int, int] | None:
    if recent_events:
        first = recent_events[0]
        return int(first.chat_id), int(first.topic_id)
    for window in historical_windows:
        if window:
            first = window[0]
            return int(first.chat_id), int(first.topic_id)
    return None


def _in_scope(event: MemoryEvent, scope: tuple[int, int] | None) -> bool:
    if scope is None:
        return True
    return (int(event.chat_id), int(event.topic_id)) == scope


def build_culture_context(
    recent_events: Sequence[MemoryEvent],
    historical_windows: Sequence[Sequence[MemoryEvent]],
    *,
    trigger_text: str | None = None,
    textual_event_count: int = 0,
    bootstrap_threshold: int = 10_000,
    weight_seed: int = 0,
) -> CultureGenerationContext:
    """Build weighted local-generation input while preserving turn semantics."""

    scope = _scope_from_inputs(recent_events, historical_windows)
    recent = sorted(
        (event for event in recent_events if _in_scope(event, scope)),
        key=_event_order_key,
    )
    historical = [
        sorted(
            (event for event in window if _in_scope(event, scope)),
            key=_event_order_key,
        )
        for window in historical_windows
        if window
    ]
    historical = [window for window in historical if window]
    threshold = max(1, int(bootstrap_threshold))
    mature_corpus = int(textual_event_count) >= threshold
    seed = int(weight_seed)

    source_messages: list[str] = []
    recent_runs = build_conversation_runs(recent)
    for run in recent_runs:
        _add_run_relations(
            run,
            source_messages,
            base_weight=_RECENT_SOURCE_WEIGHT,
            mature_corpus=mature_corpus,
            weight_seed=seed,
        )

    historical_count = sum(len(window) for window in historical)
    for window in historical:
        for run in build_conversation_runs(window):
            _add_run_relations(
                run,
                source_messages,
                base_weight=_HISTORICAL_SOURCE_WEIGHT,
                mature_corpus=mature_corpus,
                weight_seed=seed,
            )

    context_messages = [
        text
        for event in recent
        if _include_source(event, mature_corpus=mature_corpus, weight_seed=seed)
        if (text := _event_text(event)) is not None
    ][-_CONTEXT_LIMIT:]

    cleaned_trigger = " ".join(trigger_text.split()).strip() if isinstance(trigger_text, str) else ""
    if cleaned_trigger and not contains_prohibited_language(cleaned_trigger):
        context_messages.extend([cleaned_trigger, cleaned_trigger])

    emoji_candidates: list[str] = []
    emoji_term_scores: defaultdict[str, Counter[str]] = defaultdict(Counter)

    def add_emoji_culture(event: MemoryEvent, weight: int) -> None:
        if not _include_source(event, mature_corpus=mature_corpus, weight_seed=seed):
            return
        emojis = _emoji_from_event(event)
        if not emojis:
            return
        event_terms = _terms(_event_text(event))
        for emoji in emojis:
            emoji_candidates.extend([emoji] * max(1, int(weight)))
            for term in event_terms:
                emoji_term_scores[emoji][term] += max(1, int(weight))

    for event in recent:
        add_emoji_culture(event, _RECENT_SOURCE_WEIGHT)
    for window in historical:
        for event in window:
            add_emoji_culture(event, _HISTORICAL_SOURCE_WEIGHT)

    return CultureGenerationContext(
        source_messages=source_messages,
        context_messages=context_messages,
        emoji_candidates=emoji_candidates,
        emoji_term_scores={emoji: dict(scores) for emoji, scores in emoji_term_scores.items()},
        recent_event_count=len(recent),
        historical_event_count=historical_count,
        conversation_run_count=len(recent_runs),
    )


def apply_emoji_style(
    text: str,
    context: CultureGenerationContext,
    *,
    recent_signatures: set[str],
    rng: random.Random,
) -> tuple[str, str | None]:
    """Optionally add 0–2 locally learned emoji without making them mandatory."""

    cleaned = text.strip()
    if not cleaned or not context.emoji_candidates:
        return text, None
    if rng.random() >= _EMOJI_STYLE_CHANCE:
        return text, None

    anchor_terms = _terms(cleaned)
    for message in context.context_messages[-8:]:
        anchor_terms.update(_terms(message))

    frequencies = Counter(context.emoji_candidates)
    scores: dict[str, int] = {}
    for emoji, frequency in frequencies.items():
        score = int(frequency)
        term_scores = context.emoji_term_scores.get(emoji, {})
        score += sum(int(term_scores.get(term, 0)) * 3 for term in anchor_terms)
        scores[emoji] = score

    recent_emojis = {
        emoji
        for signature in recent_signatures
        for emoji in _EMOJI_RE.findall(signature)
    }
    alternatives = [emoji for emoji in scores if emoji not in recent_emojis]
    pool = alternatives or list(scores)
    if not pool:
        return text, None

    best_score = max(scores[emoji] for emoji in pool)
    competitive = [
        emoji
        for emoji in pool
        if scores[emoji] >= max(1, int(best_score * 0.75))
    ]
    competitive.sort(key=lambda emoji: (-scores[emoji], emoji))

    first_score = scores[competitive[0]]
    top = [emoji for emoji in competitive if scores[emoji] == first_score]
    first = rng.choice(top)
    chosen = [first]

    if len(competitive) > 1 and rng.random() < 0.25:
        remaining = [emoji for emoji in competitive if emoji != first]
        if remaining:
            second_best = max(scores[emoji] for emoji in remaining)
            second_pool = [emoji for emoji in remaining if scores[emoji] == second_best]
            chosen.append(rng.choice(second_pool))

    signature = "".join(chosen[:2])
    if not signature or signature in recent_signatures:
        return text, None
    return f"{cleaned} {signature}", signature


__all__ = [
    "CultureGenerationContext",
    "CultureMemorySnapshot",
    "DEFAULT_RUN_GAP_SECONDS",
    "apply_emoji_style",
    "build_conversation_runs",
    "build_culture_context",
]

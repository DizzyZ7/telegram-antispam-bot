"""Pure contextual ranking for remembered Culture Memory media."""

from __future__ import annotations

import random
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from .culture import build_conversation_runs
from .language import analyze_token
from .models import EntertainmentActionRecord, EntertainmentActionType, MemoryEvent, MemoryEventType

_SUPPORTED_MEDIA = {
    MemoryEventType.STICKER,
    MemoryEventType.PHOTO,
    MemoryEventType.ANIMATION,
}
_THRESHOLDS = {
    MemoryEventType.STICKER: 0.48,
    MemoryEventType.ANIMATION: 0.62,
    MemoryEventType.PHOTO: 0.78,
}
_MIN_EXACT_REPEAT_GAP_SECONDS = 300
_RANDOM_SCORE_BAND = 0.18
_LIVE_RANDOM_SCORE_BAND = 0.05
_WORD_RE = re.compile(r"[^\W\d_]{2,}", re.UNICODE)
_EMOJI_RE = re.compile(
    "["
    "\U0001F1E6-\U0001F1FF"
    "\U0001F300-\U0001FAFF"
    "\u2600-\u27BF"
    "]",
    re.UNICODE,
)


@dataclass(frozen=True, slots=True)
class MediaCandidate:
    event: MemoryEvent
    score: float
    source_class: str
    score_bucket: str


def _terms(value: str | None) -> set[str]:
    if not value:
        return set()
    terms: set[str] = set()
    for match in _WORD_RE.findall(value):
        surface = match.casefold()
        terms.add(surface)
        analysed = analyze_token(surface)
        lemma = (analysed.lemma or analysed.normalized).casefold()
        if lemma:
            terms.add(lemma)
    return terms


def _event_text(event: MemoryEvent) -> str | None:
    if event.event_type in {MemoryEventType.TEXT, MemoryEventType.EMOJI}:
        value = event.text
    else:
        value = event.caption
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split()).strip()
    return cleaned or None


def _scope(
    recent_events: Sequence[MemoryEvent],
    historical_windows: Sequence[Sequence[MemoryEvent]],
) -> tuple[int, int] | None:
    if recent_events:
        return int(recent_events[0].chat_id), int(recent_events[0].topic_id)
    for window in historical_windows:
        if window:
            return int(window[0].chat_id), int(window[0].topic_id)
    return None


def _same_scope(event: MemoryEvent, scope: tuple[int, int] | None) -> bool:
    if scope is None:
        return False
    return (int(event.chat_id), int(event.topic_id)) == scope


def _media_usage_ages(
    actions: Sequence[EntertainmentActionRecord],
    *,
    now: int,
    repeat_cooldown_seconds: int,
) -> dict[str, int]:
    """Return youngest in-window use age for each remembered media id.

    The historical cooldown is now a soft diversity window. Only a very recent
    exact repeat is hard-blocked; older uses remain selectable with a temporary
    weight penalty so a small sticker pool never becomes unusable for hours.
    """

    cutoff = max(0, int(repeat_cooldown_seconds))
    ages: dict[str, int] = {}
    for action in actions:
        if action.action_type is not EntertainmentActionType.MEMORY_CALLBACK:
            continue
        age = int(now) - int(action.created_at)
        if age < 0 or age > cutoff:
            continue
        value = action.metadata.get("media_file_unique_id")
        if not isinstance(value, str) or not value:
            continue
        previous = ages.get(value)
        if previous is None or age < previous:
            ages[value] = age
    return ages


def _reuse_weight(age: int | None, repeat_cooldown_seconds: int) -> float:
    if age is None:
        return 1.0
    if age < _MIN_EXACT_REPEAT_GAP_SECONDS:
        return 0.0
    cooldown = max(_MIN_EXACT_REPEAT_GAP_SECONDS + 1, int(repeat_cooldown_seconds))
    progress = min(1.0, max(0.0, age / cooldown))
    return 0.35 + 0.65 * progress


def _candidate_context_terms(
    candidate: MemoryEvent,
    run: Sequence[MemoryEvent],
) -> tuple[set[str], bool]:
    terms = set(_terms(candidate.caption))
    if candidate.sticker_set_name:
        terms.update(_terms(candidate.sticker_set_name.replace("_", " ")))

    explicit_reply = False
    by_message_id = {
        int(item.message_id): item
        for item in run
        if item.message_id is not None
    }
    index = next((idx for idx, item in enumerate(run) if item is candidate), None)
    if index is not None:
        for neighbor in run[max(0, index - 2) : min(len(run), index + 3)]:
            if neighbor is candidate:
                continue
            terms.update(_terms(_event_text(neighbor)))

    if candidate.reply_to_message_id is not None:
        target = by_message_id.get(int(candidate.reply_to_message_id))
        if target is not None:
            terms.update(_terms(_event_text(target)))
            explicit_reply = True

    if candidate.message_id is not None:
        candidate_id = int(candidate.message_id)
        for item in run:
            if item.reply_to_message_id == candidate_id:
                terms.update(_terms(_event_text(item)))
                explicit_reply = True

    return terms, explicit_reply


def _score_bucket(score: float) -> str:
    if score >= 0.85:
        return "high"
    if score >= 0.65:
        return "medium"
    return "low"


def _raw_score(
    event: MemoryEvent,
    *,
    candidate_terms: set[str],
    anchor_terms: set[str],
    context_text: str,
    is_recent: bool,
    explicit_reply: bool,
    frequency: int,
    mature_corpus: bool,
) -> float:
    if not anchor_terms or not candidate_terms:
        return 0.0

    overlap = len(anchor_terms & candidate_terms)
    if overlap <= 0:
        return 0.0

    containment = overlap / max(1, min(len(anchor_terms), len(candidate_terms)))
    union = len(anchor_terms | candidate_terms)
    jaccard = overlap / max(1, union)

    score = 0.68 * containment + 0.22 * jaccard
    score += 0.08 if is_recent else 0.02
    if explicit_reply:
        score += 0.08
    if frequency > 1:
        score += min(0.08, 0.02 * (frequency - 1))
    if event.sticker_emoji and event.sticker_emoji in context_text:
        score += 0.06

    if mature_corpus and event.sender_is_bot:
        score *= 0.40

    return max(0.0, min(1.0, score))


def _live_rng(scope: tuple[int, int], now: int) -> random.Random:
    """Return a changing but reproducible local RNG without global state."""
    chat_id, topic_id = scope
    seed = (
        (abs(int(chat_id)) * 31)
        ^ (int(topic_id) * 131)
        ^ int(now)
    ) & 0x7FFFFFFF
    return random.Random(seed)


def select_media_candidate(
    recent_events: Sequence[MemoryEvent],
    historical_windows: Sequence[Sequence[MemoryEvent]],
    *,
    context_messages: Sequence[str],
    recent_actions: Sequence[EntertainmentActionRecord],
    now: int,
    textual_event_count: int,
    bootstrap_threshold: int,
    repeat_cooldown_seconds: int,
    rng: random.Random | None = None,
) -> MediaCandidate | None:
    """Select one relevant remembered media item with bounded weighted variety."""

    scope = _scope(recent_events, historical_windows)
    if scope is None:
        return None

    anchor_text = " ".join(str(item) for item in context_messages[-8:] if item)
    anchor_terms = _terms(anchor_text)
    if not anchor_terms:
        return None

    usage_ages = _media_usage_ages(
        recent_actions,
        now=int(now),
        repeat_cooldown_seconds=int(repeat_cooldown_seconds),
    )
    threshold = max(1, int(bootstrap_threshold))
    mature_corpus = int(textual_event_count) >= threshold

    scoped_recent = [item for item in recent_events if _same_scope(item, scope)]
    scoped_historical = [
        [item for item in window if _same_scope(item, scope)]
        for window in historical_windows
    ]
    scoped_historical = [window for window in scoped_historical if window]

    all_scoped = list(scoped_recent)
    for window in scoped_historical:
        all_scoped.extend(window)
    frequencies = Counter(
        item.file_unique_id
        for item in all_scoped
        if item.event_type in _SUPPORTED_MEDIA and item.file_unique_id
    )

    best_by_unique: dict[str, tuple[MediaCandidate, bool]] = {}

    def consider_run(run: Sequence[MemoryEvent], *, is_recent: bool) -> None:
        for item in run:
            if item.event_type not in _SUPPORTED_MEDIA:
                continue
            if item.is_forwarded or not item.file_id or not item.file_unique_id:
                continue
            if _reuse_weight(
                usage_ages.get(item.file_unique_id),
                int(repeat_cooldown_seconds),
            ) <= 0.0:
                continue

            candidate_terms, explicit_reply = _candidate_context_terms(item, run)
            score = _raw_score(
                item,
                candidate_terms=candidate_terms,
                anchor_terms=anchor_terms,
                context_text=anchor_text,
                is_recent=is_recent,
                explicit_reply=explicit_reply,
                frequency=int(frequencies[item.file_unique_id]),
                mature_corpus=mature_corpus,
            )
            if score < _THRESHOLDS[item.event_type]:
                continue

            candidate = MediaCandidate(
                event=item,
                score=score,
                source_class="other_bot" if item.sender_is_bot else "human",
                score_bucket=_score_bucket(score),
            )
            previous = best_by_unique.get(item.file_unique_id)
            if previous is None:
                best_by_unique[item.file_unique_id] = (candidate, is_recent)
                continue
            old, old_recent = previous
            if (score, int(is_recent), int(item.created_at), int(item.message_id or -1)) > (
                old.score,
                int(old_recent),
                int(old.event.created_at),
                int(old.event.message_id or -1),
            ):
                best_by_unique[item.file_unique_id] = (candidate, is_recent)

    for run in build_conversation_runs(scoped_recent):
        consider_run(run, is_recent=True)
    for window in scoped_historical:
        for run in build_conversation_runs(window):
            consider_run(run, is_recent=False)

    if not best_by_unique:
        return None

    ranked = list(best_by_unique.values())
    best_score = max(pair[0].score for pair in ranked)
    score_band = _RANDOM_SCORE_BAND if rng is not None else _LIVE_RANDOM_SCORE_BAND
    band = [pair for pair in ranked if pair[0].score >= best_score - score_band]

    if len(band) == 1:
        return band[0][0]

    choice_rng = rng if rng is not None else _live_rng(scope, int(now))
    weights: list[float] = []
    for candidate, is_recent in band:
        unique_id = candidate.event.file_unique_id
        reuse = _reuse_weight(
            usage_ages.get(unique_id or ""),
            int(repeat_cooldown_seconds),
        )
        recency = 1.08 if is_recent else 1.0
        weights.append(max(0.01, candidate.score * reuse * recency))

    return choice_rng.choices([pair[0] for pair in band], weights=weights, k=1)[0]


__all__ = ["MediaCandidate", "select_media_candidate"]
"""Pure contextual ranking for remembered Culture Memory media."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from .culture import build_conversation_runs
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
    return {match.casefold() for match in _WORD_RE.findall(value)}


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


def _blocked_media_ids(
    actions: Sequence[EntertainmentActionRecord],
    *,
    now: int,
    repeat_cooldown_seconds: int,
) -> set[str]:
    cutoff = max(0, int(repeat_cooldown_seconds))
    blocked: set[str] = set()
    for action in actions:
        if action.action_type is not EntertainmentActionType.MEMORY_CALLBACK:
            continue
        age = int(now) - int(action.created_at)
        if age < 0 or age > cutoff:
            continue
        value = action.metadata.get("media_file_unique_id")
        if isinstance(value, str) and value:
            blocked.add(value)
    return blocked


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
) -> MediaCandidate | None:
    """Select one contextually relevant remembered media item from bounded inputs."""

    scope = _scope(recent_events, historical_windows)
    if scope is None:
        return None

    anchor_text = " ".join(str(item) for item in context_messages[-8:] if item)
    anchor_terms = _terms(anchor_text)
    if not anchor_terms:
        return None

    blocked_ids = _blocked_media_ids(
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
            if item.file_unique_id in blocked_ids:
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

    return max(
        best_by_unique.values(),
        key=lambda pair: (
            pair[0].score,
            int(pair[1]),
            int(pair[0].event.created_at),
            int(pair[0].event.message_id or -1),
        ),
    )[0]


__all__ = ["MediaCandidate", "select_media_candidate"]

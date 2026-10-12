"""Conservative, topic-local slang discovery from existing Culture Memory.

No external service, profile or permanent second dictionary. Repeated harmless
slang is re-derived from the same chat/topic snapshot, so deletion and opt-out
are honored automatically. Unknown expressions require an explicit community
"word/meme of the day" introduction plus independent repetition.
"""
from __future__ import annotations

import random
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher

from .models import MemoryEvent, MemoryEventType
from .moderation_safety import is_safe_entertainment_reply, is_safe_entertainment_text

_TOKEN_RE = re.compile(r"(?<![\w@/])[а-яё]{4,18}(?![\w])", re.I)
_NEW_TERM_RE = re.compile(
    r"\b(?:слово\s+дня|мем\s+дня|новый\s+мем|"
    r"называем\s+это|это\s+теперь)\s*[:—–-]?\s*([а-яё]{4,18})\b",
    re.I,
)
_LINK_OR_MENTION = re.compile(r"(?:https?://|www\.|t\.me/|@[A-Za-z0-9_]+)", re.I)
_KNOWN_HARMLESS = frozenset({
    "угар", "рофл", "рофлы", "вайб", "кайф", "жиза", "кринж",
    "чилл", "имба", "база", "мемас", "мемасы", "топчик",
    "легендарно", "гениально", "приколюха", "котомем",
})
_TEMPLATES = (
    "«{term}» уже звучит как официальный термин этого чата.",
    "У «{term}» явно начинается своя сюжетная арка.",
    "Кажется, «{term}» пора выдавать отдельную прописку в чате.",
    "«{term}» — очередной экспонат в музее местных мемов.",
)
_RECENT_SECONDS = 20 * 60
_SUPPORTED = frozenset({MemoryEventType.TEXT, MemoryEventType.EMOJI})
_MAX_EVENTS = 1600


@dataclass(frozen=True, slots=True)
class SlangTerm:
    value: str
    mentions: int
    speakers: int
    introduced: bool


def _normalize(value: str) -> str:
    return " ".join(re.findall(r"[a-zа-яё0-9]+", value.casefold()))


def _too_similar(candidate: str, prior: set[str]) -> bool:
    normalized = _normalize(candidate)
    return normalized in prior or any(
        min(len(normalized), len(item)) >= 30
        and SequenceMatcher(None, normalized, item).ratio() >= 0.86
        for item in prior
    )


def learn_topic_slang(
    recent_events: Sequence[MemoryEvent],
    historical_windows: Sequence[Sequence[MemoryEvent]],
    *,
    now: int,
) -> tuple[SlangTerm, ...]:
    """Discover candidate terms, requiring real human usage in this topic.

    Pre-approved harmless slang: >=2 messages by >=2 speakers; novel words:
    >=3 independent messages by >=2 speakers AND an explicit meme/word naming.
    A term must be present in a fresh human message before it can be spoken.
    """
    if not recent_events:
        return ()
    scope = (int(recent_events[0].chat_id), int(recent_events[0].topic_id))
    selected: dict[tuple[int, int], MemoryEvent] = {}
    for event in [
        *recent_events[-_MAX_EVENTS:],
        *(item for window in historical_windows for item in window),
    ]:
        if (int(event.chat_id), int(event.topic_id)) != scope:
            continue
        if event.event_type not in _SUPPORTED or event.sender_is_bot or event.is_forwarded or event.is_command:
            continue
        raw = event.text
        if not isinstance(raw, str) or not 5 <= len(raw) <= 350:
            continue
        if _LINK_OR_MENTION.search(raw) or not is_safe_entertainment_text(raw):
            continue
        ident = (int(event.user_id), int(event.message_id or event.id or event.created_at))
        selected[ident] = event

    appearances: Counter[str] = Counter()
    speakers: dict[str, set[int]] = defaultdict(set)
    introduced: set[str] = set()
    fresh: set[str] = set()
    for event in selected.values():
        text = event.text or ""
        words = {word.casefold() for word in _TOKEN_RE.findall(text)}
        declared = {word.casefold() for word in _NEW_TERM_RE.findall(text)}
        introduced.update(declared)
        age = int(now) - int(event.created_at)
        for term in words:
            if not is_safe_entertainment_reply(f"«{term}» уже звучит как термин."):
                continue
            appearances[term] += 1
            speakers[term].add(int(event.user_id))
            if 0 <= age <= _RECENT_SECONDS:
                fresh.add(term)

    terms = []
    for term in fresh:
        count = appearances[term]
        user_count = len(speakers[term])
        known = term in _KNOWN_HARMLESS
        if user_count < 2 or count < (2 if known else 3):
            continue
        if not known and term not in introduced:
            continue
        terms.append(SlangTerm(term, count, user_count, term in introduced))
    # Keep the candidate set bounded and deterministic, without any identity data.
    terms.sort(key=lambda item: (-item.speakers, -item.mentions, item.value))
    return tuple(terms[:12])


def choose_slang_callback(
    terms: Sequence[SlangTerm],
    *,
    recent_outputs: Iterable[str],
    rng: random.Random,
) -> tuple[str, str] | None:
    """One safe conversational callback for an already approved text slot."""
    if not terms:
        return None
    prior = {_normalize(text) for text in recent_outputs if isinstance(text, str) and text}
    options: list[tuple[str, str]] = []
    for term in terms[:12]:
        for template in _TEMPLATES:
            line = template.format(term=term.value)
            if is_safe_entertainment_reply(line) and not _too_similar(line, prior):
                options.append((line, term.value))
    if not options:
        return None
    return rng.choice(options)


__all__ = ["SlangTerm", "choose_slang_callback", "learn_topic_slang"]

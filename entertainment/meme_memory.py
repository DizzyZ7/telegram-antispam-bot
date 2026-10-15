"""Privacy-aware local meme recall, derived from existing chat Culture Memory.

A meme is one of Fosgen's OWN verified past lines that at least two distinct
consenting chat participants reacted to. User texts/identities are not copied
into actions, persisted as meme scores, or reused in bot output.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import Sequence

from .coherence import generated_text_defect
from .models import EntertainmentActionRecord, MemoryEvent, MemoryEventType
from .moderation_safety import is_safe_entertainment_reply

MEME_LOOKBACK_SECONDS = 7 * 24 * 3600
MEME_REACTION_WINDOW_SECONDS = 20 * 60
MEME_RECALL_DELAY_SECONDS = 30 * 60
MEME_ACTION_LIMIT = 360
MEME_EVENT_LIMIT = 1500

_WORDS = re.compile(r"[а-яёa-z]{3,}", re.I)
_REACTION = re.compile(r"ахах|хаха|пха+ха|пхах|лол|ржу|🤣|😂|😭", re.I)
_CUE = re.compile(r"\b(?:мем|помн|легенд|вспомн|тот\s+самый|опять|снова)\w*\b", re.I)
_EXCLUDED = frozenset({
    "это", "уже", "что", "сегодня", "здесь", "вот", "потом",
    "просто", "всего", "такой", "вчера", "снова", "опять",
})


def _terms(value: str) -> set[str]:
    return {
        term for word in _WORDS.findall(value.casefold())
        if (term := word.replace("ё", "е")) not in _EXCLUDED
        and len(term) >= 5
    }


def _quoted_echo(message: str, phrase: str) -> bool:
    phrase_terms = _terms(phrase)
    shared = phrase_terms & _terms(message)
    # Two distinctive shared words are enough for short reordered quotes
    # like "Овации внеплановые", without full exact-sentence matching.
    return len(shared) >= 2 and len(shared) / min(max(len(phrase_terms), 1), 4) >= 0.5


def could_recall_meme(text: str | None) -> bool:
    if not isinstance(text, str) or not text.strip():
        return False
    return bool(_CUE.search(text)) or len(_terms(text)) >= 2


@dataclass(frozen=True, slots=True)
class LocalMeme:
    bot_text: str
    supporter_count: int
    score: int
    last_reaction_at: int


def rank_local_memes(
    actions: Sequence[EntertainmentActionRecord],
    events: Sequence[MemoryEvent],
    *,
    chat_id: int,
    topic_id: int,
    now: int,
) -> tuple[LocalMeme, ...]:
    """Require 2 independent consented humans, no copied human messages."""
    verified: list[tuple[str, int, int, int]] = []
    for action in actions:
        if int(action.chat_id) != int(chat_id) or int(action.topic_id) != int(topic_id):
            continue
        if not 0 <= now - int(action.created_at) <= MEME_LOOKBACK_SECONDS:
            continue
        meta = action.metadata
        if not isinstance(meta, dict) or meta.get("output_kind") != "text":
            continue
        if meta.get("dialogue_intent") == "meme":
            continue
        phrase = meta.get("output")
        bot_message_id = meta.get("bot_message_id")
        if (not isinstance(phrase, str) or not 18 <= len(phrase) <= 140
                or not is_safe_entertainment_reply(phrase)
                or generated_text_defect(phrase)
                or not isinstance(bot_message_id, int) or bot_message_id <= 0):
            continue
        verified.append((phrase, int(bot_message_id), int(action.created_at), int(action.id or 0)))
    if not verified:
        return ()

    # Aggregate by original bot phrase; only one positive vote per person.
    scores: dict[str, dict[int, tuple[int, int]]] = {}
    for event in events:
        if (int(event.chat_id) != int(chat_id) or int(event.topic_id) != int(topic_id)
                or event.sender_is_bot or event.is_forwarded or event.is_command
                or event.event_type not in {MemoryEventType.TEXT, MemoryEventType.EMOJI}):
            continue
        message = event.text or ""
        if not message or len(message) > 400:
            continue
        for phrase, bot_msg_id, created, _action_id in verified:
            if not 0 <= int(event.created_at) - created <= MEME_REACTION_WINDOW_SECONDS:
                continue
            is_reply = event.reply_to_message_id == bot_msg_id
            is_echo = _quoted_echo(message, phrase)
            if not is_reply and not is_echo:
                continue
            if not (_REACTION.search(message) or is_echo):
                continue
            # A quoted catchphrase (especially without reply threading) is
            # stronger evidence than a generic laughing reply.
            strength = 3 if is_echo else 1
            existing = scores.setdefault(phrase, {}).get(int(event.user_id))
            if existing is None or strength > existing[0]:
                scores[phrase][int(event.user_id)] = (strength, int(event.created_at))

    memes = [
        LocalMeme(
            bot_text=phrase,
            supporter_count=len(votes),
            score=sum(weight for weight, _at in votes.values()),
            last_reaction_at=max(at for _w, at in votes.values()),
        )
        for phrase, votes in scores.items()
        if len(votes) >= 2
    ]
    return tuple(sorted(
        memes,
        key=lambda m: (-m.supporter_count, -m.score, -m.last_reaction_at, m.bot_text),
    ))


def choose_meme_callback(
    actions: Sequence[EntertainmentActionRecord],
    events: Sequence[MemoryEvent],
    *,
    chat_id: int,
    topic_id: int,
    now: int,
    trigger_text: str,
    recent_outputs: Sequence[str],
    rng: random.Random,
) -> str | None:
    """Only an explicit reminder or recognizable echo may revive a meme."""
    if not could_recall_meme(trigger_text):
        return None
    cue = bool(_CUE.search(trigger_text))
    recent_normalized = {" ".join(text.casefold().split()) for text in recent_outputs}
    matches = []
    for meme in rank_local_memes(
        actions, events, chat_id=chat_id, topic_id=topic_id, now=now,
    ):
        if now - meme.last_reaction_at < MEME_RECALL_DELAY_SECONDS:
            continue
        if not _quoted_echo(trigger_text, meme.bot_text) and not (
            cue and bool(_terms(trigger_text) & _terms(meme.bot_text))
        ):
            continue
        if " ".join(meme.bot_text.casefold().split()) in recent_normalized:
            continue
        matches.append(meme)
    if not matches:
        return None
    meme = matches[0]
    variants = (
        f"«{meme.bot_text}» — это уже местная классика 😂",
        f"Кажется, «{meme.bot_text}» теперь отдельный мем чата.",
    )
    safe = [line for line in variants if is_safe_entertainment_reply(line)]
    if not safe:
        return None
    return rng.choice(safe)


__all__ = [
    "LocalMeme", "MEME_LOOKBACK_SECONDS", "MEME_ACTION_LIMIT", "MEME_EVENT_LIMIT",
    "could_recall_meme", "rank_local_memes", "choose_meme_callback",
]

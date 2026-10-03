"""Bounded linguistic signals for the chat-only generation engine.

This module deliberately treats morphology as a soft hint. Telegram chat
language contains commands, nicknames, English words and slang that must remain
usable even when pymorphy3 cannot analyse them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from types import MappingProxyType
from typing import Iterable, Mapping

import pymorphy3

_TOKEN_RE = re.compile(
    r"/?[A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9_'’-]*",
    re.UNICODE,
)
_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]", re.UNICODE)

# This is intentionally compact: TopicAnchor needs to remove grammatical/noise
# words, not behave as a literary-language dictionary.
_STOPWORDS = frozenset(
    {
        "а",
        "без",
        "бы",
        "в",
        "во",
        "вот",
        "вы",
        "где",
        "да",
        "для",
        "до",
        "его",
        "ее",
        "её",
        "же",
        "за",
        "и",
        "из",
        "или",
        "их",
        "к",
        "как",
        "ли",
        "мне",
        "мы",
        "на",
        "наш",
        "не",
        "ни",
        "но",
        "ну",
        "о",
        "об",
        "он",
        "она",
        "они",
        "от",
        "по",
        "про",
        "с",
        "со",
        "так",
        "там",
        "то",
        "тот",
        "тут",
        "ты",
        "у",
        "уже",
        "что",
        "эта",
        "это",
        "этот",
        "я",
    }
)

_MORPH: pymorphy3.MorphAnalyzer | None = None


def _morph() -> pymorphy3.MorphAnalyzer:
    global _MORPH
    if _MORPH is None:
        _MORPH = pymorphy3.MorphAnalyzer()
    return _MORPH


def _normalize_surface(value: str) -> str:
    return value.strip().lstrip("/@").casefold()


@dataclass(frozen=True, slots=True)
class AnalyzedToken:
    surface: str
    normalized: str
    lemma: str
    pos: str | None
    morph_confident: bool


@lru_cache(maxsize=4096)
def analyze_token(text: str) -> AnalyzedToken:
    """Return a bounded, fail-soft analysis for one chat token."""

    surface = str(text).strip()
    normalized = _normalize_surface(surface)
    if not normalized:
        return AnalyzedToken(surface, "", "", None, False)

    if not _CYRILLIC_RE.search(normalized):
        return AnalyzedToken(surface, normalized, normalized, None, False)

    try:
        parses = _morph().parse(normalized)
    except Exception:
        return AnalyzedToken(surface, normalized, normalized, None, False)

    if not parses:
        return AnalyzedToken(surface, normalized, normalized, None, False)

    best = parses[0]
    lemma = (best.normal_form or normalized).casefold()
    pos = getattr(best.tag, "POS", None)
    confidence = float(getattr(best, "score", 0.0) or 0.0)
    return AnalyzedToken(
        surface=surface,
        normalized=normalized,
        lemma=lemma,
        pos=str(pos) if pos else None,
        morph_confident=confidence >= 0.25,
    )


def _message_key(text: str) -> str:
    return " ".join(str(text).split()).strip().casefold()


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(str(text))


def _content_analysis(text: str) -> list[AnalyzedToken]:
    result: list[AnalyzedToken] = []
    for raw in _tokens(text):
        analysed = analyze_token(raw)
        key = analysed.lemma or analysed.normalized
        if len(analysed.normalized) < 2 or key in _STOPWORDS:
            continue
        result.append(analysed)
    return result


@dataclass(frozen=True, slots=True)
class TopicAnchor:
    lemma_weights: Mapping[str, float]
    surface_weights: Mapping[str, float]
    phrase_weights: Mapping[tuple[str, ...], float]
    trigger_terms: frozenset[str]

    def relevance(self, tokens: Iterable[str]) -> float:
        """Return a soft 0..1 topical overlap signal for candidate tokens."""

        analyses = [analyze_token(token) for token in tokens]
        if not analyses or (not self.lemma_weights and not self.surface_weights):
            return 0.0

        matched = 0.0
        ceiling = 0.0
        maximum = max(
            [*self.lemma_weights.values(), *self.surface_weights.values(), 1.0]
        )
        for token in analyses:
            lemma_weight = self.lemma_weights.get(token.lemma, 0.0)
            surface_weight = self.surface_weights.get(token.normalized, 0.0)
            weight = max(lemma_weight, surface_weight)
            if weight > 0.0:
                matched += min(weight, maximum)
            ceiling += maximum
        if ceiling <= 0.0:
            return 0.0
        return min(matched / ceiling, 1.0)


def build_topic_anchor(
    context_messages: list[str],
    trigger_text: str | None,
    direct_reply: bool,
) -> TopicAnchor:
    """Build a bounded current-topic anchor from at most 40 recent messages.

    Phase C intentionally duplicated a direct trigger in legacy context to bias
    v2. V3 removes exact copies before context weighting and adds the trigger as
    its own strongest signal exactly once.
    """

    recent = list(context_messages[-40:])
    trigger_key = _message_key(trigger_text) if trigger_text else ""
    if direct_reply and trigger_key:
        recent = [message for message in recent if _message_key(message) != trigger_key]

    lemma_weights: dict[str, float] = {}
    surface_weights: dict[str, float] = {}
    phrase_weights: dict[tuple[str, ...], float] = {}

    count = max(1, len(recent))
    for index, message in enumerate(recent):
        # Recent messages carry more influence, while older entries in the
        # bounded window remain useful for short-running conversations.
        recency_weight = 0.65 + 0.85 * ((index + 1) / count)
        analysed = _content_analysis(message)
        for token in analysed:
            lemma_weights[token.lemma] = lemma_weights.get(token.lemma, 0.0) + recency_weight
            surface_weights[token.normalized] = (
                surface_weights.get(token.normalized, 0.0) + recency_weight
            )
        phrase = tuple(token.lemma for token in analysed)
        for size in (2, 3):
            if len(phrase) < size:
                continue
            for start in range(len(phrase) - size + 1):
                chunk = phrase[start : start + size]
                phrase_weights[chunk] = phrase_weights.get(chunk, 0.0) + recency_weight

    trigger_terms: set[str] = set()
    if direct_reply and trigger_key:
        for token in _content_analysis(trigger_text or ""):
            # The direct trigger is the strongest semantic signal and is added
            # once regardless of how many legacy copies occur in context.
            lemma_weights[token.lemma] = lemma_weights.get(token.lemma, 0.0) + 4.0
            surface_weights[token.normalized] = surface_weights.get(token.normalized, 0.0) + 4.0
            trigger_terms.add(token.lemma)

    return TopicAnchor(
        lemma_weights=MappingProxyType(lemma_weights),
        surface_weights=MappingProxyType(surface_weights),
        phrase_weights=MappingProxyType(phrase_weights),
        trigger_terms=frozenset(trigger_terms),
    )


__all__ = [
    "AnalyzedToken",
    "TopicAnchor",
    "analyze_token",
    "build_topic_anchor",
]

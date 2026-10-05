"""Local no-copy and moderation protection for generated entertainment text."""

from __future__ import annotations

import re

from writers_moderation import contains_prohibited_language

_WORD_RE = re.compile(r"[^\W_]+(?:['’-][^\W_]+)*", re.UNICODE)


def _normalized_words(text: str) -> tuple[str, ...]:
    return tuple(match.group(0).casefold() for match in _WORD_RE.finditer(text or ""))


def _four_grams(words: tuple[str, ...]) -> tuple[tuple[str, str, str, str], ...]:
    if len(words) < 4:
        return ()
    return tuple(tuple(words[index : index + 4]) for index in range(len(words) - 3))


def is_novel_generated_text(
    candidate: str,
    sources: list[str],
    recent_outputs: list[str],
) -> bool:
    """Reject prohibited text, replay, recent bot replay, and excessive source overlap."""
    if contains_prohibited_language(candidate):
        return False

    candidate_words = _normalized_words(candidate)
    if not candidate_words:
        return False

    normalized_candidate = " ".join(candidate_words)
    for text in (*sources, *recent_outputs):
        if normalized_candidate == " ".join(_normalized_words(text)):
            return False

    candidate_grams = _four_grams(candidate_words)
    if not candidate_grams:
        return True

    candidate_gram_count = len(candidate_grams)
    for source in sources:
        source_grams = set(_four_grams(_normalized_words(source)))
        if not source_grams:
            continue
        overlap = sum(1 for gram in candidate_grams if gram in source_grams)
        if overlap / candidate_gram_count >= 0.70:
            return False

    return True


__all__ = ["is_novel_generated_text"]

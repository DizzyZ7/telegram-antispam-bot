"""Tiny conservative quality gate for locally recombined Fosgen chat text.

This only inspects *bot-generated* candidates, never censors humans or
removes chat memory. It rejects a few observable mechanical pathologies
rather than pretending to understand semantics without a language model.
"""

from __future__ import annotations

import re

_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё'-]*")
_FAKE_INTERNAL_STATUS = re.compile(
    r"\b(?:лимит\w*|квот\w*)\s+(?:генера\w*|токен\w*|ответ\w*)\b"
    r"|\b(?:чат|бот|сервер)\s+(?:достиг\w*|исчерпал\w*)\s+(?:лимит\w*|квот\w*)\b",
    re.IGNORECASE,
)
_BROKEN_SWITCH = re.compile(
    r"\bа\s+нет\s+(?:утро|вечер|день|чат|бот)\b",
    re.IGNORECASE,
)
# Unintentional duplicated content stems: лимита лимит, генерации генерация.
# Benign rhetorical repetitions are tolerated only for a small authored list.
_RHETORICAL = frozenset({"очень", "прямо", "скоро", "ладно", "даже"})


def generated_text_defect(text: str | None) -> str | None:
    """Return a reason for a reliably observable low-quality output."""
    if not isinstance(text, str) or not text.strip():
        return "empty"
    if _FAKE_INTERNAL_STATUS.search(text):
        return "fake_internal_status"
    if _BROKEN_SWITCH.search(text):
        return "broken_clause_join"

    words = [word.casefold().replace("ё", "е") for word in _WORD_RE.findall(text)]
    for left, right in zip(words, words[1:]):
        if (
            len(left) >= 4
            and len(right) >= 4
            and left[:5] == right[:5]
            and left not in _RHETORICAL
        ):
            return "adjacent_word_stems"

    return None


__all__ = ["generated_text_defect"]

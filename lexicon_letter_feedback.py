"""Pure helpers for explaining why a Lexicon word cannot be built."""

from __future__ import annotations

from collections import Counter


def missing_letter_counts(word: str, source_word: str) -> tuple[tuple[str, int], ...]:
    """Return letters missing from source_word, including repeated-letter deficits."""
    missing = Counter(word) - Counter(source_word)
    return tuple(sorted((letter, count) for letter, count in missing.items() if count > 0))


def format_missing_letters(missing: tuple[tuple[str, int], ...]) -> str:
    """Format deficits as ``к`` or ``а×2, к`` for a Telegram HTML message."""
    return ", ".join(letter if count == 1 else f"{letter}×{count}" for letter, count in missing)


def missing_letters_label(missing: tuple[tuple[str, int], ...]) -> str:
    """Return the grammatically appropriate Russian label."""
    total = sum(count for _letter, count in missing)
    return "буквы" if total == 1 else "букв"


__all__ = ["format_missing_letters", "missing_letter_counts", "missing_letters_label"]

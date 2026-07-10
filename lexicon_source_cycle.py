"""Pure source-cycle selection helpers for the Lexicon game.

The module has no Telegram, database, file-system, or startup side effects.
Persistence is handled by the Lexicon service; this module only chooses the next
word from the still-unused part of a cycle.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Iterable, Sequence

ChoiceFunction = Callable[[Sequence[str]], str]


def unique_words(words: Iterable[str]) -> list[str]:
    """Return words in original order without duplicates or empty values."""
    result: list[str] = []
    seen: set[str] = set()
    for word in words:
        if not word or word in seen:
            continue
        seen.add(word)
        result.append(word)
    return result


def choose_cycle_word(
    candidates: Iterable[str],
    used_words: set[str],
    last_word: str | None,
    *,
    chooser: ChoiceFunction = random.choice,
) -> tuple[str, bool]:
    """Choose a source without repeating it inside the current cycle.

    Returns ``(word, cycle_restarted)``. When the current cycle is exhausted,
    all candidates become available again. If there is more than one candidate,
    the first word of the new cycle cannot equal the final word of the previous
    cycle.
    """
    pool = unique_words(candidates)
    if not pool:
        raise ValueError("Lexicon source cycle has no candidates")

    available = [word for word in pool if word not in used_words]
    if available:
        return chooser(available), False

    restarted_pool = pool
    if last_word is not None and len(pool) > 1:
        without_last = [word for word in pool if word != last_word]
        if without_last:
            restarted_pool = without_last

    return chooser(restarted_pool), True

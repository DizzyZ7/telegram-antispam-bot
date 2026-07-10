"""Small, dependency-free raccoon feature for Lexicon rounds."""

from __future__ import annotations

from collections import Counter
from typing import Any

RACCOON_WORD = "енот"


def raccoon_can_hide(base_word: str) -> bool:
    """Return whether all letters of «енот» exist in the source word."""
    source = Counter(base_word.lower().replace("ё", "е"))
    target = Counter(RACCOON_WORD)
    return all(source[letter] >= count for letter, count in target.items())


def raccoon_finder_name(round_data: Any) -> str | None:
    """Return the player name who first claimed «енот», when it was found."""
    user_id = getattr(round_data, "used_words", {}).get(RACCOON_WORD)
    if user_id is None:
        return None
    player = getattr(round_data, "players", {}).get(user_id)
    if player is None:
        return None
    name = getattr(player, "name", None)
    return str(name) if name else None


__all__ = ["RACCOON_WORD", "raccoon_can_hide", "raccoon_finder_name"]

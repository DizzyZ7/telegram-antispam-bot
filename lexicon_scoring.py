"""Balanced scoring rules for the Lexicon mini-game.

The number of discovered words must remain the main source of success. Word
length is rewarded, but its bonus is capped so a few very long words cannot
outscore dozens of valid short findings.
"""

from __future__ import annotations

from typing import Any

BALANCED_SCORING_TEXT = (
    "Очки за слова — с мягким бонусом за длину:\n"
    "4 буквы — 1🌟\n"
    "5–6 букв — 2🌟\n"
    "7–8 букв — 3🌟\n"
    "9–10 букв — 4🌟\n"
    "11+ букв — 5🌟 максимум"
)


def points_for_length(length: int) -> int:
    """Return a capped score for one accepted word."""
    if length < 4:
        return 0
    if length == 4:
        return 1
    if length <= 6:
        return 2
    if length <= 8:
        return 3
    if length <= 10:
        return 4
    return 5


def balanced_word_points(word: str) -> int:
    return points_for_length(len(word))


def longest_word_length(player: Any) -> int:
    words = getattr(player, "words", {})
    return max((len(word) for word in words), default=0)


def player_rank_key(player: Any) -> tuple[int, int, int, str]:
    """Sort best player first: points, breadth, longest find, then name."""
    points = int(getattr(player, "points", 0))
    words = getattr(player, "words", {})
    name = str(getattr(player, "name", "")).lower()
    return (-points, -len(words), -longest_word_length(player), name)

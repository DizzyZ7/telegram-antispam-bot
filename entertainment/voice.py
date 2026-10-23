"""Small, local variation of the bot's delivery without canned catchphrases.

Never alters content words, adds unsolicited jokes, or changes question intent.
"""
from __future__ import annotations

import random
import re

_TERMINAL = re.compile(r"([.!?…])$")


def choose_voice_variant(text: str, recent_outputs: list[str], *, rng: random.Random) -> str:
    """Vary punctuation only when the recent bot cadence is visibly repetitive."""
    cleaned = text.strip()
    if not cleaned or not recent_outputs:
        return text
    ending = _TERMINAL.search(cleaned)
    if ending is None or ending.group(1) != ".":
        return text

    # Require three consecutive period-terminated outputs. A rare exclamation
    # breaks monotony, but never rewrites a question or adds an emoji.
    last_three = recent_outputs[:3]
    if len(last_three) < 3 or not all(item.rstrip().endswith(".") for item in last_three):
        return text
    if rng.random() < 0.20:
        return cleaned[:-1] + "!"
    return text


__all__ = ["choose_voice_variant"]

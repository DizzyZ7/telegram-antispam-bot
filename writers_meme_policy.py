"""Owner-requested word policy shared by moderation and Entertainment safety."""
from __future__ import annotations

import re

_MEME_WORD = re.compile(
    r'(?<!\w)могн(?:ул[аои]?|[её](?:шь|т|м|те)|у(?:ть|т)?|и(?:те)?)(?!\w)',
    re.IGNORECASE,
)


def contains_meme_words(text: str | None) -> bool:
    return isinstance(text, str) and _MEME_WORD.search(text) is not None


def rewrite_meme_words(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        source = match.group()
        replacement = 'муркн' + source[4:].lower()
        if source.isupper():
            return replacement.upper()
        return replacement.capitalize() if source[0].isupper() else replacement

    return _MEME_WORD.sub(replace, text)

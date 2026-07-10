"""Isolated chat scope for the Lexicon mini-game.

Chats listed here are visible only to the Lexicon service. They are deliberately
not added to legacy_main.ALLOWED_CHATS, so moderation, summaries, statistics,
captcha and unrelated bot commands stay disabled there.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

LEXICON_ONLY_CHAT_IDS: frozenset[int] = frozenset({-1002659916114})


class LexiconGameAppScope:
    """Delegate the full app API while extending only Lexicon chat access."""

    __slots__ = ("_base_app", "_lexicon_only_chat_ids")

    def __init__(self, base_app: Any, lexicon_only_chat_ids: Iterable[int]) -> None:
        self._base_app = base_app
        self._lexicon_only_chat_ids = frozenset(int(chat_id) for chat_id in lexicon_only_chat_ids)

    @property
    def lexicon_only_chat_ids(self) -> frozenset[int]:
        return self._lexicon_only_chat_ids

    def is_allowed_chat(self, chat_id: int) -> bool:
        return bool(
            self._base_app.is_allowed_chat(chat_id)
            or int(chat_id) in self._lexicon_only_chat_ids
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._base_app, name)


__all__ = ["LEXICON_ONLY_CHAT_IDS", "LexiconGameAppScope"]

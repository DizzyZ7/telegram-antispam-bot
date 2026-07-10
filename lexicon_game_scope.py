"""Isolated chat scope for the Lexicon mini-game.

Chats listed here are visible only to the Lexicon service. They are deliberately
not added to legacy_main.ALLOWED_CHATS, so moderation, summaries, statistics,
captcha and unrelated bot commands stay disabled there.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from aiogram.filters import BaseFilter
from aiogram.types import Message

LOGGER = logging.getLogger(__name__)
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


class LexiconOnlyChatFilter(BaseFilter):
    """Match messages from chats that must expose only the Lexicon game."""

    __slots__ = ("chat_ids",)

    def __init__(self, chat_ids: Iterable[int]) -> None:
        self.chat_ids = frozenset(int(chat_id) for chat_id in chat_ids)

    async def __call__(self, message: Message) -> bool:
        return int(message.chat.id) in self.chat_ids


def register_lexicon_only_guard(app: Any, chat_ids: Iterable[int]) -> None:
    """Stop Lexicon-only chat messages before legacy handlers see them.

    Registration order matters: this guard is promoted first, then the Lexicon
    command handlers are promoted above it. As a result, Lexicon commands execute,
    ordinary guesses are processed by the Lexicon outer middleware, and the guard
    consumes everything afterward without invoking unrelated bot functionality.
    """

    normalized_ids = frozenset(int(chat_id) for chat_id in chat_ids)
    if not normalized_ids:
        return

    dispatcher = app.dp

    @dispatcher.message(LexiconOnlyChatFilter(normalized_ids))
    async def lexicon_only_chat_guard(message: Message) -> None:
        LOGGER.debug(
            "Lexicon-only guard consumed message chat_id=%s message_id=%s",
            message.chat.id,
            message.message_id,
        )

    # Put the guard ahead of all legacy handlers. register_minigame_handlers() is
    # called afterward and promotes only the Lexicon command handlers above it.
    dispatcher.message.handlers.insert(0, dispatcher.message.handlers.pop())
    print(
        "LEXICON_ONLY_GUARD_READY "
        f"chat_ids={','.join(str(chat_id) for chat_id in sorted(normalized_ids))} "
        "legacy_handlers=blocked",
        flush=True,
    )


__all__ = [
    "LEXICON_ONLY_CHAT_IDS",
    "LexiconGameAppScope",
    "LexiconOnlyChatFilter",
    "register_lexicon_only_guard",
]

"""Automatic pin lifecycle for Lexicon round source messages.

This final wrapper remembers the exact message pinned for each chat/topic scope and
unpins only that message after the round finishes. Other chat pins are never touched.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiogram.types import Message

from lexicon_learning_raccoon import (
    LearningLexiconService as BaseLearningLexiconService,
    register_lexicon_learning_handlers,
)

LOGGER = logging.getLogger(__name__)
GameKey = tuple[int, int | None]


class LearningLexiconService(BaseLearningLexiconService):
    """Production Lexicon service with exact automatic unpinning."""

    def __init__(self, app: Any, storage: Any) -> None:
        super().__init__(app, storage)
        self._lexicon_pin_message_ids: dict[GameKey, int] = {}
        self._lexicon_pin_ready: dict[GameKey, asyncio.Event] = {}

    async def pin_start_message(self, message: Message) -> None:
        """Remember the exact source message while the existing pin logic runs.

        The readiness event closes a small race where /stopgame could be processed
        while Telegram is still completing the pin request.
        """
        game_key = self.round_key_from_message(message)
        ready = asyncio.Event()
        self._lexicon_pin_message_ids[game_key] = int(message.message_id)
        self._lexicon_pin_ready[game_key] = ready

        try:
            await super().pin_start_message(message)
        finally:
            ready.set()

    async def _unpin_round_message(self, game_key: GameKey, chat_id: int) -> bool:
        """Unpin the exact remembered source message and clear its registry entry."""
        ready = self._lexicon_pin_ready.get(game_key)
        if ready is not None:
            await ready.wait()

        message_id = self._lexicon_pin_message_ids.pop(game_key, None)
        self._lexicon_pin_ready.pop(game_key, None)
        if message_id is None:
            return False

        try:
            await self.app.bot.unpin_chat_message(
                chat_id=chat_id,
                message_id=message_id,
            )
        except Exception as exc:
            # A failed original pin, manually removed pin or missing rights must not
            # break final results, database writes or raccoon collection persistence.
            LOGGER.info(
                "Could not unpin Lexicon source message chat_id=%s message_id=%s: %s",
                chat_id,
                message_id,
                exc,
            )
            return False

        LOGGER.info(
            "LEXICON_SOURCE_UNPINNED chat_id=%s thread_id=%s message_id=%s",
            chat_id,
            game_key[1],
            message_id,
        )
        return True

    async def finish_word_game(
        self,
        chat_id: int,
        message_thread_id: int | None,
        forced: bool,
    ) -> None:
        """Finish normally, then remove only this round's source pin."""
        game_key = self.round_key(chat_id, message_thread_id)
        try:
            await super().finish_word_game(chat_id, message_thread_id, forced)
        finally:
            await self._unpin_round_message(game_key, chat_id)


print(
    "LEXICON_AUTO_UNPIN_READY exact_message=on automatic=on forced_stop=on other_pins=preserved",
    flush=True,
)

__all__ = ["LearningLexiconService", "register_lexicon_learning_handlers"]

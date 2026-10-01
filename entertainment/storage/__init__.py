"""Entertainment storage backends and transitional compatibility wrapper."""

from __future__ import annotations

from typing import Any

from ..config import GENERATION_SAMPLE_LIMIT
from .base import EntertainmentStorage as EntertainmentStorageProtocol
from .sqlite import SQLiteEntertainmentStorage


class LegacyCompatibleEntertainmentStorage(SQLiteEntertainmentStorage):
    """Preserve the v1 constructor/API while service code moves to topic-aware calls."""

    async def add_message(self, chat_id: int, *args: Any, **kwargs: Any) -> None:
        if "topic_id" in kwargs:
            await super().add_message(chat_id=chat_id, **kwargs)
            return
        if len(args) == 2 and isinstance(args[1], str):
            user_id, text = args
            await super().add_message(chat_id, 0, int(user_id), text)
            return
        if len(args) >= 3:
            topic_id, user_id, text = args[:3]
            message_id = kwargs.get("message_id")
            await super().add_message(
                chat_id,
                int(topic_id),
                int(user_id),
                str(text),
                message_id=message_id,
            )
            return
        raise TypeError("add_message expects v1 (chat_id, user_id, text) or v2 topic-aware arguments")

    async def recent_messages(
        self,
        chat_id: int,
        topic_id: int = 0,
        limit: int = GENERATION_SAMPLE_LIMIT,
    ) -> list[str]:
        return await super().recent_messages(chat_id, topic_id, limit)

    async def clear_chat(self, chat_id: int) -> int:
        return await self.clear_scope(chat_id, None)


__all__ = [
    "EntertainmentStorageProtocol",
    "LegacyCompatibleEntertainmentStorage",
    "SQLiteEntertainmentStorage",
]

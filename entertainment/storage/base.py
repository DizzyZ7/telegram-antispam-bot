"""Storage contract for entertainment persistence backends."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..config import GENERATION_SAMPLE_LIMIT
from ..models import EntertainmentSettings


@runtime_checkable
class EntertainmentStorage(Protocol):
    async def initialize(self) -> None: ...

    async def close(self) -> None: ...

    async def get_settings(self, chat_id: int) -> EntertainmentSettings: ...

    async def save_settings(self, chat_id: int, settings: EntertainmentSettings) -> None: ...

    async def add_message(
        self,
        chat_id: int,
        topic_id: int,
        user_id: int,
        text: str,
        *,
        message_id: int | None = None,
    ) -> None: ...

    async def recent_messages(
        self,
        chat_id: int,
        topic_id: int,
        limit: int = GENERATION_SAMPLE_LIMIT,
    ) -> list[str]: ...

    async def message_count(self, chat_id: int, topic_id: int | None = None) -> int: ...

    async def clear_scope(self, chat_id: int, topic_id: int | None = None) -> int: ...

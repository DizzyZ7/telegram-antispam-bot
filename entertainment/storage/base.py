"""Storage contract for entertainment persistence backends."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..config import GENERATION_SAMPLE_LIMIT
from ..context import ActivitySnapshot
from ..models import EntertainmentActionRecord, EntertainmentSettings

LegacySettingsRow = tuple[int, bool, int, int, int]
LegacyMessageRow = tuple[int, int, int, str, int]


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
        created_at: int | None = None,
    ) -> None: ...

    async def recent_messages(
        self,
        chat_id: int,
        topic_id: int,
        limit: int = GENERATION_SAMPLE_LIMIT,
    ) -> list[str]: ...

    async def message_count(self, chat_id: int, topic_id: int | None = None) -> int: ...

    async def clear_scope(self, chat_id: int, topic_id: int | None = None) -> int: ...

    async def activity_snapshot(
        self,
        chat_id: int,
        topic_id: int,
        *,
        now: int,
    ) -> ActivitySnapshot: ...

    async def record_action(self, record: EntertainmentActionRecord) -> int: ...

    async def recent_actions(
        self,
        chat_id: int,
        topic_id: int,
        *,
        since: int,
        limit: int = 20,
    ) -> list[EntertainmentActionRecord]: ...

    async def human_messages_since(self, chat_id: int, topic_id: int, *, since: int) -> int: ...

    async def is_migration_applied(self, migration_key: str) -> bool: ...

    async def import_legacy_batch(
        self,
        migration_key: str,
        settings_rows: list[LegacySettingsRow],
        message_rows: list[LegacyMessageRow],
    ) -> tuple[int, int]: ...

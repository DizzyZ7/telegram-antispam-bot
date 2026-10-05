"""Bounded chronological Culture Memory window sampling."""

from __future__ import annotations

import random

from ..models import MemoryEvent
from .retention import (
    PostgresEntertainmentStorage as RetentionPostgresEntertainmentStorage,
    SQLiteEntertainmentStorage as RetentionSQLiteEntertainmentStorage,
    _EVENT_FIELDS,
    _delete_count,
    _event_from_values,
)


def _window_starts(total: int, window_count: int, window_size: int, seed: int) -> tuple[int, list[int]]:
    total = max(0, int(total))
    if total <= 0:
        return 0, []
    size = min(max(1, int(window_size)), total)
    possible_starts = total - size + 1
    if possible_starts <= 1:
        return size, [0]
    count = min(max(1, int(window_count)), possible_starts)
    starts = sorted(random.Random(int(seed)).sample(range(possible_starts), count))
    return size, starts


class SQLiteEntertainmentStorage(RetentionSQLiteEntertainmentStorage):
    """Retention SQLite backend plus deterministic bounded historical windows."""

    async def delete_message_memory(self, chat_id: int, message_id: int) -> int:
        """Atomically forget one Telegram message from both text projections."""
        connection = self._require_connection()
        await connection.execute("BEGIN IMMEDIATE")
        try:
            event_cursor = await connection.execute(
                "DELETE FROM ent_memory_events WHERE chat_id = ? AND message_id = ?",
                (int(chat_id), int(message_id)),
            )
            legacy_cursor = await connection.execute(
                "DELETE FROM entertainment_messages WHERE chat_id = ? AND message_id = ?",
                (int(chat_id), int(message_id)),
            )
            deleted = max(0, int(event_cursor.rowcount)) + max(0, int(legacy_cursor.rowcount))
            await connection.commit()
            return deleted
        except Exception:
            await connection.rollback()
            raise

    async def sample_event_windows(
        self,
        chat_id: int,
        topic_id: int,
        *,
        window_count: int,
        window_size: int,
        seed: int,
    ) -> list[list[MemoryEvent]]:
        connection = self._require_connection()
        async with connection.execute(
            "SELECT COUNT(*) FROM ent_memory_events WHERE chat_id = ? AND topic_id = ?",
            (int(chat_id), int(topic_id)),
        ) as cursor:
            row = await cursor.fetchone()
        total = int(row[0]) if row else 0
        size, starts = _window_starts(total, window_count, window_size, seed)
        windows: list[list[MemoryEvent]] = []
        for offset in starts:
            async with connection.execute(
                f"SELECT {_EVENT_FIELDS} FROM ent_memory_events "
                "WHERE chat_id = ? AND topic_id = ? "
                "ORDER BY created_at ASC, message_id ASC, id ASC LIMIT ? OFFSET ?",
                (int(chat_id), int(topic_id), size, int(offset)),
            ) as cursor:
                rows = await cursor.fetchall()
            window = [
                event
                for raw in rows
                if (event := _event_from_values(tuple(raw))) is not None
            ]
            if window:
                windows.append(window)
        return windows


class PostgresEntertainmentStorage(RetentionPostgresEntertainmentStorage):
    """Retention PostgreSQL backend plus deterministic bounded historical windows."""

    async def delete_message_memory(self, chat_id: int, message_id: int) -> int:
        """Atomically forget one Telegram message from both text projections."""
        pool = self._require_pool()
        async with pool.acquire() as connection:
            async with connection.transaction():
                event_status = await connection.execute(
                    "DELETE FROM ent_memory_events WHERE chat_id=$1 AND message_id=$2",
                    int(chat_id),
                    int(message_id),
                )
                legacy_status = await connection.execute(
                    "DELETE FROM entertainment_messages WHERE chat_id=$1 AND message_id=$2",
                    int(chat_id),
                    int(message_id),
                )
        return _delete_count(event_status) + _delete_count(legacy_status)

    async def sample_event_windows(
        self,
        chat_id: int,
        topic_id: int,
        *,
        window_count: int,
        window_size: int,
        seed: int,
    ) -> list[list[MemoryEvent]]:
        pool = self._require_pool()
        total = int(
            await pool.fetchval(
                "SELECT COUNT(*) FROM ent_memory_events WHERE chat_id=$1 AND topic_id=$2",
                int(chat_id),
                int(topic_id),
            )
            or 0
        )
        size, starts = _window_starts(total, window_count, window_size, seed)
        windows: list[list[MemoryEvent]] = []
        for offset in starts:
            rows = await pool.fetch(
                f"SELECT {_EVENT_FIELDS} FROM ent_memory_events "
                "WHERE chat_id=$1 AND topic_id=$2 "
                "ORDER BY created_at ASC, message_id ASC, id ASC LIMIT $3 OFFSET $4",
                int(chat_id),
                int(topic_id),
                size,
                int(offset),
            )
            window = [
                event
                for raw in rows
                if (event := _event_from_values(tuple(raw))) is not None
            ]
            if window:
                windows.append(window)
        return windows


__all__ = ["PostgresEntertainmentStorage", "SQLiteEntertainmentStorage"]

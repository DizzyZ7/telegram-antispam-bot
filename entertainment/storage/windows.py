"""Bounded chronological Culture Memory window sampling."""

from __future__ import annotations

import random

from ..models import MemoryEvent
from .retention import (
    PostgresEntertainmentStorage as RetentionPostgresEntertainmentStorage,
    SQLiteEntertainmentStorage as RetentionSQLiteEntertainmentStorage,
    _EVENT_FIELDS,
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

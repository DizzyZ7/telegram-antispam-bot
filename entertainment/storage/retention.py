"""Long-term retention wrappers and Culture Memory storage helpers."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from ..config import GENERATION_SAMPLE_LIMIT, MEMORY_LIMIT, MEMORY_PRUNE_BUFFER
from ..models import MemoryCounts, MemoryEvent, MemoryEventType
from .postgres import PostgresEntertainmentStorage as CorePostgresEntertainmentStorage
from .sqlite import SQLiteEntertainmentStorage as CoreSQLiteEntertainmentStorage

LOGGER = logging.getLogger(__name__)
_EVENT_FIELDS = (
    "id, chat_id, topic_id, message_id, user_id, event_type, text, caption, "
    "reply_to_message_id, file_id, file_unique_id, sticker_emoji, sticker_set_name, "
    "media_width, media_height, media_duration, is_forwarded, legacy_source_id, metadata_json, created_at"
)


def _parse_metadata(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _event_from_values(values: list[object] | tuple[object, ...]) -> MemoryEvent | None:
    try:
        event_type = MemoryEventType(str(values[5]))
    except ValueError:
        LOGGER.warning("Skipping unknown Culture Memory event type id=%s type=%r", values[0], values[5])
        return None
    return MemoryEvent(
        id=int(values[0]),
        chat_id=int(values[1]),
        topic_id=int(values[2]),
        message_id=int(values[3]) if values[3] is not None else None,
        user_id=int(values[4]),
        event_type=event_type,
        text=str(values[6]) if values[6] is not None else None,
        caption=str(values[7]) if values[7] is not None else None,
        reply_to_message_id=int(values[8]) if values[8] is not None else None,
        file_id=str(values[9]) if values[9] is not None else None,
        file_unique_id=str(values[10]) if values[10] is not None else None,
        sticker_emoji=str(values[11]) if values[11] is not None else None,
        sticker_set_name=str(values[12]) if values[12] is not None else None,
        media_width=int(values[13]) if values[13] is not None else None,
        media_height=int(values[14]) if values[14] is not None else None,
        media_duration=int(values[15]) if values[15] is not None else None,
        is_forwarded=bool(values[16]),
        legacy_source_id=int(values[17]) if values[17] is not None else None,
        metadata=_parse_metadata(values[18]),
        created_at=int(values[19]),
    )


def _event_args(event: MemoryEvent) -> tuple[object, ...]:
    return (
        int(event.chat_id),
        int(event.topic_id),
        int(event.message_id) if event.message_id is not None else None,
        int(event.user_id),
        event.event_type.value,
        event.text,
        event.caption,
        int(event.reply_to_message_id) if event.reply_to_message_id is not None else None,
        event.file_id,
        event.file_unique_id,
        event.sticker_emoji,
        event.sticker_set_name,
        int(event.media_width) if event.media_width is not None else None,
        int(event.media_height) if event.media_height is not None else None,
        int(event.media_duration) if event.media_duration is not None else None,
        bool(event.is_forwarded),
        int(event.legacy_source_id) if event.legacy_source_id is not None else None,
        json.dumps(event.metadata, ensure_ascii=False, separators=(",", ":")),
        int(event.created_at),
    )


def _delete_count(status: str) -> int:
    try:
        return int(status.rsplit(" ", 1)[-1])
    except (TypeError, ValueError):
        return 0


class SQLiteEntertainmentStorage(CoreSQLiteEntertainmentStorage):
    """SQLite backend with configurable, batched topic retention."""

    def __init__(
        self,
        database_path: Path,
        *,
        memory_limit: int = MEMORY_LIMIT,
        prune_buffer: int = MEMORY_PRUNE_BUFFER,
    ) -> None:
        super().__init__(database_path)
        self.memory_limit = max(1, int(memory_limit))
        self.prune_buffer = max(1, int(prune_buffer))

    async def initialize(self) -> None:
        await super().initialize()
        connection = self._require_connection()
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS ent_memory_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                topic_id INTEGER NOT NULL DEFAULT 0,
                message_id INTEGER,
                user_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                text TEXT,
                caption TEXT,
                reply_to_message_id INTEGER,
                file_id TEXT,
                file_unique_id TEXT,
                sticker_emoji TEXT,
                sticker_set_name TEXT,
                media_width INTEGER,
                media_height INTEGER,
                media_duration INTEGER,
                is_forwarded INTEGER NOT NULL DEFAULT 0,
                legacy_source_id INTEGER,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at INTEGER NOT NULL
            )
            """
        )
        await connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_ent_memory_chat_message "
            "ON ent_memory_events(chat_id, message_id) WHERE message_id IS NOT NULL"
        )
        await connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_ent_memory_legacy_source "
            "ON ent_memory_events(legacy_source_id) WHERE legacy_source_id IS NOT NULL"
        )
        await connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_ent_memory_scope_order "
            "ON ent_memory_events(chat_id, topic_id, created_at DESC, message_id DESC, id DESC)"
        )
        await connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_ent_memory_user "
            "ON ent_memory_events(chat_id, user_id, id DESC)"
        )
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS ent_memory_preferences (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                remember_enabled INTEGER NOT NULL DEFAULT 1,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY(chat_id, user_id)
            )
            """
        )
        await connection.commit()

    async def add_message(
        self,
        chat_id: int,
        topic_id: int,
        user_id: int,
        text: str,
        *,
        message_id: int | None = None,
        created_at: int | None = None,
    ) -> None:
        connection = self._require_connection()
        timestamp = int(time.time()) if created_at is None else int(created_at)
        await connection.execute(
            """
            INSERT INTO entertainment_messages(chat_id, topic_id, message_id, user_id, text, created_at)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (int(chat_id), int(topic_id), message_id, int(user_id), text, timestamp),
        )
        async with connection.execute(
            "SELECT COUNT(*) FROM entertainment_messages WHERE chat_id = ? AND topic_id = ?",
            (int(chat_id), int(topic_id)),
        ) as cursor:
            row = await cursor.fetchone()
        count = int(row[0]) if row else 0
        if count > self.memory_limit + self.prune_buffer:
            await connection.execute(
                """
                DELETE FROM entertainment_messages
                WHERE chat_id = ? AND topic_id = ?
                  AND id NOT IN (
                      SELECT id FROM entertainment_messages
                      WHERE chat_id = ? AND topic_id = ?
                      ORDER BY id DESC LIMIT ?
                  )
                """,
                (int(chat_id), int(topic_id), int(chat_id), int(topic_id), self.memory_limit),
            )
        await connection.commit()

    async def add_event(self, event: MemoryEvent) -> int:
        connection = self._require_connection()
        args = list(_event_args(event))
        args[15] = 1 if event.is_forwarded else 0
        cursor = await connection.execute(
            """
            INSERT OR IGNORE INTO ent_memory_events(
                chat_id, topic_id, message_id, user_id, event_type, text, caption,
                reply_to_message_id, file_id, file_unique_id, sticker_emoji,
                sticker_set_name, media_width, media_height, media_duration,
                is_forwarded, legacy_source_id, metadata_json, created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            tuple(args),
        )
        if cursor.rowcount > 0 and cursor.lastrowid is not None:
            event_id = int(cursor.lastrowid)
        else:
            event_id = await self._resolve_sqlite_event_id(event)
        await self._prune_sqlite_events(event.chat_id, event.topic_id)
        await connection.commit()
        return event_id

    async def _resolve_sqlite_event_id(self, event: MemoryEvent) -> int:
        connection = self._require_connection()
        if event.message_id is not None:
            async with connection.execute(
                "SELECT id FROM ent_memory_events WHERE chat_id = ? AND message_id = ?",
                (int(event.chat_id), int(event.message_id)),
            ) as cursor:
                row = await cursor.fetchone()
            if row is not None:
                return int(row[0])
        if event.legacy_source_id is not None:
            async with connection.execute(
                "SELECT id FROM ent_memory_events WHERE legacy_source_id = ?",
                (int(event.legacy_source_id),),
            ) as cursor:
                row = await cursor.fetchone()
            if row is not None:
                return int(row[0])
        raise RuntimeError("SQLite Culture Memory insert was ignored without a resolvable row")

    async def _prune_sqlite_events(self, chat_id: int, topic_id: int) -> None:
        connection = self._require_connection()
        async with connection.execute(
            "SELECT COUNT(*) FROM ent_memory_events WHERE chat_id = ? AND topic_id = ?",
            (int(chat_id), int(topic_id)),
        ) as cursor:
            row = await cursor.fetchone()
        count = int(row[0]) if row else 0
        if count <= self.memory_limit + self.prune_buffer:
            return
        await connection.execute(
            """
            DELETE FROM ent_memory_events
            WHERE chat_id = ? AND topic_id = ?
              AND id NOT IN (
                  SELECT id FROM ent_memory_events
                  WHERE chat_id = ? AND topic_id = ?
                  ORDER BY created_at DESC, message_id DESC, id DESC LIMIT ?
              )
            """,
            (int(chat_id), int(topic_id), int(chat_id), int(topic_id), self.memory_limit),
        )

    async def recent_events(self, chat_id: int, topic_id: int, limit: int) -> list[MemoryEvent]:
        connection = self._require_connection()
        async with connection.execute(
            f"""
            SELECT {_EVENT_FIELDS} FROM ent_memory_events
            WHERE chat_id = ? AND topic_id = ?
            ORDER BY created_at DESC, message_id DESC, id DESC LIMIT ?
            """,
            (int(chat_id), int(topic_id), max(1, int(limit))),
        ) as cursor:
            rows = await cursor.fetchall()
        return [event for row in reversed(rows) if (event := _event_from_values(tuple(row))) is not None]

    async def recent_texts(
        self,
        chat_id: int,
        topic_id: int,
        limit: int = GENERATION_SAMPLE_LIMIT,
    ) -> list[str]:
        connection = self._require_connection()
        async with connection.execute(
            """
            SELECT CASE WHEN event_type = 'text' THEN text ELSE caption END
            FROM ent_memory_events
            WHERE chat_id = ? AND topic_id = ?
              AND ((event_type = 'text' AND text IS NOT NULL AND TRIM(text) <> '')
                   OR (event_type IN ('sticker','photo','animation') AND caption IS NOT NULL AND TRIM(caption) <> ''))
            ORDER BY created_at DESC, message_id DESC, id DESC LIMIT ?
            """,
            (int(chat_id), int(topic_id), max(1, int(limit))),
        ) as cursor:
            rows = await cursor.fetchall()
        return [str(row[0]) for row in reversed(rows)]

    async def memory_counts(self, chat_id: int, topic_id: int) -> MemoryCounts:
        connection = self._require_connection()
        async with connection.execute(
            """
            SELECT COUNT(*),
                   SUM(CASE WHEN event_type = 'text' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN event_type = 'emoji' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN event_type = 'sticker' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN event_type = 'photo' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN event_type = 'animation' THEN 1 ELSE 0 END)
            FROM ent_memory_events WHERE chat_id = ? AND topic_id = ?
            """,
            (int(chat_id), int(topic_id)),
        ) as cursor:
            row = await cursor.fetchone()
        values = tuple(int(value or 0) for value in (row or (0, 0, 0, 0, 0, 0)))
        return MemoryCounts(*values)

    async def get_remember_enabled(self, chat_id: int, user_id: int) -> bool:
        connection = self._require_connection()
        async with connection.execute(
            "SELECT remember_enabled FROM ent_memory_preferences WHERE chat_id = ? AND user_id = ?",
            (int(chat_id), int(user_id)),
        ) as cursor:
            row = await cursor.fetchone()
        return True if row is None else bool(row[0])

    async def set_remember_enabled(self, chat_id: int, user_id: int, enabled: bool) -> None:
        connection = self._require_connection()
        await connection.execute(
            """
            INSERT INTO ent_memory_preferences(chat_id, user_id, remember_enabled, updated_at)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(chat_id, user_id) DO UPDATE SET
                remember_enabled = excluded.remember_enabled, updated_at = excluded.updated_at
            """,
            (int(chat_id), int(user_id), 1 if enabled else 0, int(time.time())),
        )
        await connection.commit()

    async def delete_user_memory(self, chat_id: int, user_id: int) -> int:
        connection = self._require_connection()
        cursor = await connection.execute(
            "DELETE FROM ent_memory_events WHERE chat_id = ? AND user_id = ?",
            (int(chat_id), int(user_id)),
        )
        await connection.commit()
        return max(0, int(cursor.rowcount))

    async def delete_legacy_user_messages(self, chat_id: int, user_id: int) -> int:
        connection = self._require_connection()
        cursor = await connection.execute(
            "DELETE FROM entertainment_messages WHERE chat_id = ? AND user_id = ?",
            (int(chat_id), int(user_id)),
        )
        await connection.commit()
        return max(0, int(cursor.rowcount))

    async def clear_memory_scope(self, chat_id: int, topic_id: int | None = None) -> int:
        connection = self._require_connection()
        if topic_id is None:
            params: tuple[int, ...] = (int(chat_id),)
            where = "chat_id = ?"
        else:
            params = (int(chat_id), int(topic_id))
            where = "chat_id = ? AND topic_id = ?"
        async with connection.execute(f"SELECT COUNT(*) FROM ent_memory_events WHERE {where}", params) as cursor:
            row = await cursor.fetchone()
        count = int(row[0]) if row else 0
        await connection.execute(f"DELETE FROM ent_memory_events WHERE {where}", params)
        await connection.commit()
        return count

    async def backfill_legacy_memory(self, migration_key: str) -> int:
        connection = self._require_connection()
        await connection.execute("BEGIN IMMEDIATE")
        imported = 0
        try:
            async with connection.execute(
                "SELECT 1 FROM ent_schema_migrations WHERE migration_key = ?",
                (str(migration_key),),
            ) as marker_cursor:
                if await marker_cursor.fetchone() is not None:
                    await connection.rollback()
                    return 0
            cursor = await connection.execute(
                """
                SELECT id, chat_id, topic_id, message_id, user_id, text, created_at
                FROM entertainment_messages ORDER BY id
                """
            )
            while True:
                batch = await cursor.fetchmany(500)
                if not batch:
                    break
                for legacy_id, chat_id, topic_id, message_id, user_id, text, created_at in batch:
                    result = await connection.execute(
                        """
                        INSERT OR IGNORE INTO ent_memory_events(
                            chat_id, topic_id, message_id, user_id, event_type, text,
                            legacy_source_id, metadata_json, created_at
                        ) VALUES(?, ?, ?, ?, 'text', ?, ?, '{}', ?)
                        """,
                        (chat_id, topic_id, message_id, user_id, text, legacy_id, created_at),
                    )
                    imported += max(0, int(result.rowcount))
            await cursor.close()
            await connection.execute(
                "INSERT INTO ent_schema_migrations(migration_key, applied_at) VALUES(?, ?)",
                (str(migration_key), int(time.time())),
            )
            await connection.commit()
            return imported
        except Exception:
            await connection.rollback()
            raise


class PostgresEntertainmentStorage(CorePostgresEntertainmentStorage):
    """PostgreSQL backend with configurable, batched topic retention."""

    def __init__(
        self,
        database_url: str,
        *,
        min_pool_size: int = 1,
        max_pool_size: int = 4,
        memory_limit: int = MEMORY_LIMIT,
        prune_buffer: int = MEMORY_PRUNE_BUFFER,
    ) -> None:
        super().__init__(database_url, min_pool_size=min_pool_size, max_pool_size=max_pool_size)
        self.memory_limit = max(1, int(memory_limit))
        self.prune_buffer = max(1, int(prune_buffer))

    async def initialize(self) -> None:
        await super().initialize()
        pool = self._require_pool()
        async with pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS ent_memory_events (
                        id BIGSERIAL PRIMARY KEY,
                        chat_id BIGINT NOT NULL,
                        topic_id BIGINT NOT NULL DEFAULT 0,
                        message_id BIGINT,
                        user_id BIGINT NOT NULL,
                        event_type TEXT NOT NULL,
                        text TEXT,
                        caption TEXT,
                        reply_to_message_id BIGINT,
                        file_id TEXT,
                        file_unique_id TEXT,
                        sticker_emoji TEXT,
                        sticker_set_name TEXT,
                        media_width INTEGER,
                        media_height INTEGER,
                        media_duration INTEGER,
                        is_forwarded BOOLEAN NOT NULL DEFAULT FALSE,
                        legacy_source_id BIGINT,
                        metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                        created_at BIGINT NOT NULL
                    )
                    """
                )
                await connection.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_ent_memory_chat_message "
                    "ON ent_memory_events(chat_id, message_id) WHERE message_id IS NOT NULL"
                )
                await connection.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_ent_memory_legacy_source "
                    "ON ent_memory_events(legacy_source_id) WHERE legacy_source_id IS NOT NULL"
                )
                await connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_ent_memory_scope_order "
                    "ON ent_memory_events(chat_id, topic_id, created_at DESC, message_id DESC, id DESC)"
                )
                await connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_ent_memory_user "
                    "ON ent_memory_events(chat_id, user_id, id DESC)"
                )
                await connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS ent_memory_preferences (
                        chat_id BIGINT NOT NULL,
                        user_id BIGINT NOT NULL,
                        remember_enabled BOOLEAN NOT NULL DEFAULT TRUE,
                        updated_at BIGINT NOT NULL,
                        PRIMARY KEY(chat_id, user_id)
                    )
                    """
                )

    async def add_message(
        self,
        chat_id: int,
        topic_id: int,
        user_id: int,
        text: str,
        *,
        message_id: int | None = None,
        created_at: int | None = None,
    ) -> None:
        pool = self._require_pool()
        timestamp = int(time.time()) if created_at is None else int(created_at)
        async with pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    INSERT INTO entertainment_messages(chat_id, topic_id, message_id, user_id, text, created_at)
                    VALUES($1, $2, $3, $4, $5, $6)
                    """,
                    int(chat_id), int(topic_id),
                    int(message_id) if message_id is not None else None,
                    int(user_id), text, timestamp,
                )
                count = int(await connection.fetchval(
                    "SELECT COUNT(*) FROM entertainment_messages WHERE chat_id = $1 AND topic_id = $2",
                    int(chat_id), int(topic_id),
                ) or 0)
                if count > self.memory_limit + self.prune_buffer:
                    await connection.execute(
                        """
                        DELETE FROM entertainment_messages
                        WHERE chat_id = $1 AND topic_id = $2
                          AND id NOT IN (
                              SELECT id FROM entertainment_messages
                              WHERE chat_id = $1 AND topic_id = $2 ORDER BY id DESC LIMIT $3
                          )
                        """,
                        int(chat_id), int(topic_id), self.memory_limit,
                    )

    async def add_event(self, event: MemoryEvent) -> int:
        pool = self._require_pool()
        args = _event_args(event)
        async with pool.acquire() as connection:
            async with connection.transaction():
                event_id = await connection.fetchval(
                    """
                    INSERT INTO ent_memory_events(
                        chat_id, topic_id, message_id, user_id, event_type, text, caption,
                        reply_to_message_id, file_id, file_unique_id, sticker_emoji,
                        sticker_set_name, media_width, media_height, media_duration,
                        is_forwarded, legacy_source_id, metadata_json, created_at
                    ) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18::jsonb,$19)
                    ON CONFLICT DO NOTHING RETURNING id
                    """,
                    *args,
                )
                if event_id is None:
                    event_id = await self._resolve_postgres_event_id(connection, event)
                await self._prune_postgres_events(connection, event.chat_id, event.topic_id)
                return int(event_id)

    async def _resolve_postgres_event_id(self, connection: Any, event: MemoryEvent) -> int:
        value = None
        if event.message_id is not None:
            value = await connection.fetchval(
                "SELECT id FROM ent_memory_events WHERE chat_id = $1 AND message_id = $2",
                int(event.chat_id), int(event.message_id),
            )
        if value is None and event.legacy_source_id is not None:
            value = await connection.fetchval(
                "SELECT id FROM ent_memory_events WHERE legacy_source_id = $1",
                int(event.legacy_source_id),
            )
        if value is None:
            raise RuntimeError("PostgreSQL Culture Memory insert was ignored without a resolvable row")
        return int(value)

    async def _prune_postgres_events(self, connection: Any, chat_id: int, topic_id: int) -> None:
        count = int(await connection.fetchval(
            "SELECT COUNT(*) FROM ent_memory_events WHERE chat_id = $1 AND topic_id = $2",
            int(chat_id), int(topic_id),
        ) or 0)
        if count <= self.memory_limit + self.prune_buffer:
            return
        await connection.execute(
            """
            DELETE FROM ent_memory_events
            WHERE chat_id = $1 AND topic_id = $2
              AND id NOT IN (
                  SELECT id FROM ent_memory_events
                  WHERE chat_id = $1 AND topic_id = $2
                  ORDER BY created_at DESC, message_id DESC, id DESC LIMIT $3
              )
            """,
            int(chat_id), int(topic_id), self.memory_limit,
        )

    async def recent_events(self, chat_id: int, topic_id: int, limit: int) -> list[MemoryEvent]:
        rows = await self._require_pool().fetch(
            f"""
            SELECT {_EVENT_FIELDS} FROM ent_memory_events
            WHERE chat_id = $1 AND topic_id = $2
            ORDER BY created_at DESC, message_id DESC, id DESC LIMIT $3
            """,
            int(chat_id), int(topic_id), max(1, int(limit)),
        )
        result: list[MemoryEvent] = []
        for row in reversed(rows):
            event = _event_from_values(tuple(row))
            if event is not None:
                result.append(event)
        return result

    async def recent_texts(
        self,
        chat_id: int,
        topic_id: int,
        limit: int = GENERATION_SAMPLE_LIMIT,
    ) -> list[str]:
        rows = await self._require_pool().fetch(
            """
            SELECT CASE WHEN event_type = 'text' THEN text ELSE caption END AS source_text
            FROM ent_memory_events
            WHERE chat_id = $1 AND topic_id = $2
              AND ((event_type = 'text' AND text IS NOT NULL AND BTRIM(text) <> '')
                   OR (event_type IN ('sticker','photo','animation') AND caption IS NOT NULL AND BTRIM(caption) <> ''))
            ORDER BY created_at DESC, message_id DESC, id DESC LIMIT $3
            """,
            int(chat_id), int(topic_id), max(1, int(limit)),
        )
        return [str(row["source_text"]) for row in reversed(rows)]

    async def memory_counts(self, chat_id: int, topic_id: int) -> MemoryCounts:
        row = await self._require_pool().fetchrow(
            """
            SELECT COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE event_type = 'text') AS text,
                   COUNT(*) FILTER (WHERE event_type = 'emoji') AS emoji,
                   COUNT(*) FILTER (WHERE event_type = 'sticker') AS sticker,
                   COUNT(*) FILTER (WHERE event_type = 'photo') AS photo,
                   COUNT(*) FILTER (WHERE event_type = 'animation') AS animation
            FROM ent_memory_events WHERE chat_id = $1 AND topic_id = $2
            """,
            int(chat_id), int(topic_id),
        )
        assert row is not None
        return MemoryCounts(*(int(row[name] or 0) for name in ("total", "text", "emoji", "sticker", "photo", "animation")))

    async def get_remember_enabled(self, chat_id: int, user_id: int) -> bool:
        value = await self._require_pool().fetchval(
            "SELECT remember_enabled FROM ent_memory_preferences WHERE chat_id = $1 AND user_id = $2",
            int(chat_id), int(user_id),
        )
        return True if value is None else bool(value)

    async def set_remember_enabled(self, chat_id: int, user_id: int, enabled: bool) -> None:
        await self._require_pool().execute(
            """
            INSERT INTO ent_memory_preferences(chat_id, user_id, remember_enabled, updated_at)
            VALUES($1, $2, $3, $4)
            ON CONFLICT(chat_id, user_id) DO UPDATE SET
                remember_enabled = EXCLUDED.remember_enabled, updated_at = EXCLUDED.updated_at
            """,
            int(chat_id), int(user_id), bool(enabled), int(time.time()),
        )

    async def delete_user_memory(self, chat_id: int, user_id: int) -> int:
        status = await self._require_pool().execute(
            "DELETE FROM ent_memory_events WHERE chat_id = $1 AND user_id = $2",
            int(chat_id), int(user_id),
        )
        return _delete_count(status)

    async def delete_legacy_user_messages(self, chat_id: int, user_id: int) -> int:
        status = await self._require_pool().execute(
            "DELETE FROM entertainment_messages WHERE chat_id = $1 AND user_id = $2",
            int(chat_id), int(user_id),
        )
        return _delete_count(status)

    async def clear_memory_scope(self, chat_id: int, topic_id: int | None = None) -> int:
        pool = self._require_pool()
        if topic_id is None:
            count = int(await pool.fetchval(
                "SELECT COUNT(*) FROM ent_memory_events WHERE chat_id = $1", int(chat_id)
            ) or 0)
            await pool.execute("DELETE FROM ent_memory_events WHERE chat_id = $1", int(chat_id))
        else:
            count = int(await pool.fetchval(
                "SELECT COUNT(*) FROM ent_memory_events WHERE chat_id = $1 AND topic_id = $2",
                int(chat_id), int(topic_id),
            ) or 0)
            await pool.execute(
                "DELETE FROM ent_memory_events WHERE chat_id = $1 AND topic_id = $2",
                int(chat_id), int(topic_id),
            )
        return count

    async def backfill_legacy_memory(self, migration_key: str) -> int:
        pool = self._require_pool()
        imported = 0
        async with pool.acquire() as connection:
            async with connection.transaction():
                if await connection.fetchval(
                    "SELECT 1 FROM ent_schema_migrations WHERE migration_key = $1",
                    str(migration_key),
                ) is not None:
                    return 0
                last_id = 0
                while True:
                    rows = await connection.fetch(
                        """
                        SELECT id, chat_id, topic_id, message_id, user_id, text, created_at
                        FROM entertainment_messages
                        WHERE id > $1 ORDER BY id LIMIT 500
                        """,
                        last_id,
                    )
                    if not rows:
                        break
                    for row in rows:
                        value = await connection.fetchval(
                            """
                            INSERT INTO ent_memory_events(
                                chat_id, topic_id, message_id, user_id, event_type, text,
                                legacy_source_id, metadata_json, created_at
                            ) VALUES($1,$2,$3,$4,'text',$5,$6,'{}'::jsonb,$7)
                            ON CONFLICT DO NOTHING RETURNING id
                            """,
                            int(row["chat_id"]), int(row["topic_id"]),
                            int(row["message_id"]) if row["message_id"] is not None else None,
                            int(row["user_id"]), str(row["text"]), int(row["id"]), int(row["created_at"]),
                        )
                        if value is not None:
                            imported += 1
                    last_id = int(rows[-1]["id"])
                await connection.execute(
                    "INSERT INTO ent_schema_migrations(migration_key, applied_at) VALUES($1, $2)",
                    str(migration_key), int(time.time()),
                )
        return imported


__all__ = ["PostgresEntertainmentStorage", "SQLiteEntertainmentStorage"]

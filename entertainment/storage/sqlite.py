"""Topic-aware SQLite entertainment storage."""

from __future__ import annotations

import time
from pathlib import Path

import aiosqlite

from ..config import GENERATION_SAMPLE_LIMIT, MEMORY_LIMIT
from ..models import EntertainmentSettings


class SQLiteEntertainmentStorage:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.connection: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = await aiosqlite.connect(self.database_path)
        await self.connection.execute("PRAGMA journal_mode=WAL;")
        await self.connection.execute("PRAGMA synchronous=NORMAL;")
        await self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS entertainment_chat_settings (
                chat_id INTEGER PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 1,
                laziness INTEGER NOT NULL DEFAULT 92,
                cooldown_seconds INTEGER NOT NULL DEFAULT 45,
                updated_at INTEGER NOT NULL
            )
            """
        )
        await self._ensure_messages_schema()
        await self.connection.commit()

    async def _ensure_messages_schema(self) -> None:
        connection = self._require_connection()
        async with connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='entertainment_messages'"
        ) as cursor:
            exists = await cursor.fetchone()

        if exists is None:
            await connection.execute(
                """
                CREATE TABLE entertainment_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    topic_id INTEGER NOT NULL DEFAULT 0,
                    message_id INTEGER,
                    user_id INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                )
                """
            )
        else:
            async with connection.execute("PRAGMA table_info(entertainment_messages)") as cursor:
                rows = await cursor.fetchall()
            columns = {str(row[1]) for row in rows}
            if "topic_id" not in columns:
                await connection.execute(
                    "ALTER TABLE entertainment_messages ADD COLUMN topic_id INTEGER NOT NULL DEFAULT 0"
                )
            if "message_id" not in columns:
                await connection.execute(
                    "ALTER TABLE entertainment_messages ADD COLUMN message_id INTEGER"
                )

        await connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_entertainment_messages_scope "
            "ON entertainment_messages(chat_id, topic_id, id DESC)"
        )

    async def close(self) -> None:
        if self.connection is None:
            return
        await self.connection.close()
        self.connection = None

    def _require_connection(self) -> aiosqlite.Connection:
        if self.connection is None:
            raise RuntimeError("Entertainment storage is not initialized")
        return self.connection

    async def get_settings(self, chat_id: int) -> EntertainmentSettings:
        connection = self._require_connection()
        async with connection.execute(
            """
            SELECT enabled, laziness, cooldown_seconds
            FROM entertainment_chat_settings
            WHERE chat_id = ?
            """,
            (int(chat_id),),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return EntertainmentSettings()
        return EntertainmentSettings(
            enabled=bool(row[0]),
            laziness=max(0, min(100, int(row[1]))),
            cooldown_seconds=max(5, int(row[2])),
        )

    async def save_settings(self, chat_id: int, settings: EntertainmentSettings) -> None:
        connection = self._require_connection()
        await connection.execute(
            """
            INSERT INTO entertainment_chat_settings(
                chat_id, enabled, laziness, cooldown_seconds, updated_at
            )
            VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                enabled = excluded.enabled,
                laziness = excluded.laziness,
                cooldown_seconds = excluded.cooldown_seconds,
                updated_at = excluded.updated_at
            """,
            (
                int(chat_id),
                1 if settings.enabled else 0,
                max(0, min(100, int(settings.laziness))),
                max(5, int(settings.cooldown_seconds)),
                int(time.time()),
            ),
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
    ) -> None:
        connection = self._require_connection()
        await connection.execute(
            """
            INSERT INTO entertainment_messages(
                chat_id, topic_id, message_id, user_id, text, created_at
            )
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (int(chat_id), int(topic_id), message_id, int(user_id), text, int(time.time())),
        )
        await connection.execute(
            """
            DELETE FROM entertainment_messages
            WHERE chat_id = ? AND topic_id = ?
              AND id NOT IN (
                  SELECT id
                  FROM entertainment_messages
                  WHERE chat_id = ? AND topic_id = ?
                  ORDER BY id DESC
                  LIMIT ?
              )
            """,
            (int(chat_id), int(topic_id), int(chat_id), int(topic_id), MEMORY_LIMIT),
        )
        await connection.commit()

    async def recent_messages(
        self,
        chat_id: int,
        topic_id: int,
        limit: int = GENERATION_SAMPLE_LIMIT,
    ) -> list[str]:
        connection = self._require_connection()
        async with connection.execute(
            """
            SELECT text
            FROM entertainment_messages
            WHERE chat_id = ? AND topic_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (int(chat_id), int(topic_id), max(1, int(limit))),
        ) as cursor:
            rows = await cursor.fetchall()
        return [str(row[0]) for row in reversed(rows)]

    async def message_count(self, chat_id: int, topic_id: int | None = None) -> int:
        connection = self._require_connection()
        if topic_id is None:
            query = "SELECT COUNT(*) FROM entertainment_messages WHERE chat_id = ?"
            params = (int(chat_id),)
        else:
            query = "SELECT COUNT(*) FROM entertainment_messages WHERE chat_id = ? AND topic_id = ?"
            params = (int(chat_id), int(topic_id))
        async with connection.execute(query, params) as cursor:
            row = await cursor.fetchone()
        return int(row[0]) if row else 0

    async def clear_scope(self, chat_id: int, topic_id: int | None = None) -> int:
        connection = self._require_connection()
        count = await self.message_count(chat_id, topic_id)
        if topic_id is None:
            await connection.execute(
                "DELETE FROM entertainment_messages WHERE chat_id = ?",
                (int(chat_id),),
            )
        else:
            await connection.execute(
                "DELETE FROM entertainment_messages WHERE chat_id = ? AND topic_id = ?",
                (int(chat_id), int(topic_id)),
            )
        await connection.commit()
        return count

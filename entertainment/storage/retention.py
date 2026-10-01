"""Long-term message retention wrappers for Entertainment storage backends."""

from __future__ import annotations

import time
from pathlib import Path

from ..config import MEMORY_LIMIT, MEMORY_PRUNE_BUFFER
from .postgres import PostgresEntertainmentStorage as CorePostgresEntertainmentStorage
from .sqlite import SQLiteEntertainmentStorage as CoreSQLiteEntertainmentStorage


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
            INSERT INTO entertainment_messages(
                chat_id, topic_id, message_id, user_id, text, created_at
            ) VALUES(?, ?, ?, ?, ?, ?)
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
                (
                    int(chat_id),
                    int(topic_id),
                    int(chat_id),
                    int(topic_id),
                    self.memory_limit,
                ),
            )
        await connection.commit()


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
        super().__init__(
            database_url,
            min_pool_size=min_pool_size,
            max_pool_size=max_pool_size,
        )
        self.memory_limit = max(1, int(memory_limit))
        self.prune_buffer = max(1, int(prune_buffer))

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
                    INSERT INTO entertainment_messages(
                        chat_id, topic_id, message_id, user_id, text, created_at
                    ) VALUES($1, $2, $3, $4, $5, $6)
                    """,
                    int(chat_id),
                    int(topic_id),
                    int(message_id) if message_id is not None else None,
                    int(user_id),
                    text,
                    timestamp,
                )
                count = int(
                    await connection.fetchval(
                        "SELECT COUNT(*) FROM entertainment_messages WHERE chat_id = $1 AND topic_id = $2",
                        int(chat_id),
                        int(topic_id),
                    )
                    or 0
                )
                if count > self.memory_limit + self.prune_buffer:
                    await connection.execute(
                        """
                        DELETE FROM entertainment_messages
                        WHERE chat_id = $1 AND topic_id = $2
                          AND id NOT IN (
                              SELECT id FROM entertainment_messages
                              WHERE chat_id = $1 AND topic_id = $2
                              ORDER BY id DESC LIMIT $3
                          )
                        """,
                        int(chat_id),
                        int(topic_id),
                        self.memory_limit,
                    )


__all__ = ["PostgresEntertainmentStorage", "SQLiteEntertainmentStorage"]

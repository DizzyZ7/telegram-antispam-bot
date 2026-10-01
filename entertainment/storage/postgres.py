"""PostgreSQL implementation of the entertainment storage contract."""

from __future__ import annotations

import time

import asyncpg

from ..config import GENERATION_SAMPLE_LIMIT, MEMORY_LIMIT
from ..models import EntertainmentSettings


class PostgresEntertainmentStorage:
    def __init__(self, database_url: str, *, min_pool_size: int = 1, max_pool_size: int = 4) -> None:
        self.database_url = database_url
        self.min_pool_size = max(1, int(min_pool_size))
        self.max_pool_size = max(self.min_pool_size, int(max_pool_size))
        self.pool: asyncpg.Pool | None = None

    async def initialize(self) -> None:
        self.pool = await asyncpg.create_pool(
            dsn=self.database_url,
            min_size=self.min_pool_size,
            max_size=self.max_pool_size,
            command_timeout=10,
        )
        pool = self._require_pool()
        async with pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS entertainment_chat_settings (
                        chat_id BIGINT PRIMARY KEY,
                        enabled BOOLEAN NOT NULL DEFAULT TRUE,
                        laziness INTEGER NOT NULL DEFAULT 92,
                        cooldown_seconds INTEGER NOT NULL DEFAULT 45,
                        updated_at BIGINT NOT NULL
                    )
                    """
                )
                await connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS entertainment_messages (
                        id BIGSERIAL PRIMARY KEY,
                        chat_id BIGINT NOT NULL,
                        topic_id BIGINT NOT NULL DEFAULT 0,
                        message_id BIGINT,
                        user_id BIGINT NOT NULL,
                        text TEXT NOT NULL,
                        created_at BIGINT NOT NULL,
                        legacy_source_id BIGINT
                    )
                    """
                )
                await connection.execute(
                    "ALTER TABLE entertainment_messages ADD COLUMN IF NOT EXISTS legacy_source_id BIGINT"
                )
                await connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_entertainment_messages_scope
                    ON entertainment_messages(chat_id, topic_id, id DESC)
                    """
                )
                await connection.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_entertainment_messages_legacy_source
                    ON entertainment_messages(legacy_source_id)
                    WHERE legacy_source_id IS NOT NULL
                    """
                )
                await connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS ent_schema_migrations (
                        migration_key TEXT PRIMARY KEY,
                        applied_at BIGINT NOT NULL
                    )
                    """
                )

    async def close(self) -> None:
        if self.pool is None:
            return
        await self.pool.close()
        self.pool = None

    def _require_pool(self) -> asyncpg.Pool:
        if self.pool is None:
            raise RuntimeError("Entertainment PostgreSQL storage is not initialized")
        return self.pool

    async def get_settings(self, chat_id: int) -> EntertainmentSettings:
        row = await self._require_pool().fetchrow(
            "SELECT enabled, laziness, cooldown_seconds FROM entertainment_chat_settings WHERE chat_id = $1",
            int(chat_id),
        )
        if row is None:
            return EntertainmentSettings()
        return EntertainmentSettings(
            enabled=bool(row["enabled"]),
            laziness=max(0, min(100, int(row["laziness"]))),
            cooldown_seconds=max(5, int(row["cooldown_seconds"])),
        )

    async def save_settings(self, chat_id: int, settings: EntertainmentSettings) -> None:
        await self._require_pool().execute(
            """
            INSERT INTO entertainment_chat_settings(
                chat_id, enabled, laziness, cooldown_seconds, updated_at
            ) VALUES($1, $2, $3, $4, $5)
            ON CONFLICT(chat_id) DO UPDATE SET
                enabled = EXCLUDED.enabled,
                laziness = EXCLUDED.laziness,
                cooldown_seconds = EXCLUDED.cooldown_seconds,
                updated_at = EXCLUDED.updated_at
            """,
            int(chat_id), bool(settings.enabled), max(0, min(100, int(settings.laziness))),
            max(5, int(settings.cooldown_seconds)), int(time.time()),
        )

    async def add_message(
        self, chat_id: int, topic_id: int, user_id: int, text: str, *, message_id: int | None = None
    ) -> None:
        pool = self._require_pool()
        async with pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    INSERT INTO entertainment_messages(
                        chat_id, topic_id, message_id, user_id, text, created_at
                    ) VALUES($1, $2, $3, $4, $5, $6)
                    """,
                    int(chat_id), int(topic_id), int(message_id) if message_id is not None else None,
                    int(user_id), text, int(time.time()),
                )
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
                    int(chat_id), int(topic_id), MEMORY_LIMIT,
                )

    async def recent_messages(
        self, chat_id: int, topic_id: int, limit: int = GENERATION_SAMPLE_LIMIT
    ) -> list[str]:
        rows = await self._require_pool().fetch(
            """
            SELECT text FROM entertainment_messages
            WHERE chat_id = $1 AND topic_id = $2
            ORDER BY id DESC LIMIT $3
            """,
            int(chat_id), int(topic_id), max(1, int(limit)),
        )
        return [str(row["text"]) for row in reversed(rows)]

    async def message_count(self, chat_id: int, topic_id: int | None = None) -> int:
        pool = self._require_pool()
        if topic_id is None:
            value = await pool.fetchval(
                "SELECT COUNT(*) FROM entertainment_messages WHERE chat_id = $1", int(chat_id)
            )
        else:
            value = await pool.fetchval(
                "SELECT COUNT(*) FROM entertainment_messages WHERE chat_id = $1 AND topic_id = $2",
                int(chat_id), int(topic_id),
            )
        return int(value or 0)

    async def clear_scope(self, chat_id: int, topic_id: int | None = None) -> int:
        pool = self._require_pool()
        count = await self.message_count(chat_id, topic_id)
        if topic_id is None:
            await pool.execute("DELETE FROM entertainment_messages WHERE chat_id = $1", int(chat_id))
        else:
            await pool.execute(
                "DELETE FROM entertainment_messages WHERE chat_id = $1 AND topic_id = $2",
                int(chat_id), int(topic_id),
            )
        return count

    async def is_migration_applied(self, migration_key: str) -> bool:
        value = await self._require_pool().fetchval(
            "SELECT 1 FROM ent_schema_migrations WHERE migration_key = $1", migration_key
        )
        return value is not None

    async def import_legacy_batch(
        self,
        migration_key: str,
        settings_rows: list[tuple[int, bool, int, int, int]],
        message_rows: list[tuple[int, int, int, str, int]],
    ) -> tuple[int, int]:
        pool = self._require_pool()
        settings_imported = 0
        messages_imported = 0
        async with pool.acquire() as connection:
            async with connection.transaction():
                exists = await connection.fetchval(
                    "SELECT 1 FROM ent_schema_migrations WHERE migration_key = $1 FOR UPDATE",
                    migration_key,
                )
                if exists is not None:
                    return 0, 0

                for chat_id, enabled, laziness, cooldown_seconds, updated_at in settings_rows:
                    status = await connection.execute(
                        """
                        INSERT INTO entertainment_chat_settings(
                            chat_id, enabled, laziness, cooldown_seconds, updated_at
                        ) VALUES($1, $2, $3, $4, $5)
                        ON CONFLICT(chat_id) DO NOTHING
                        """,
                        chat_id, enabled, laziness, cooldown_seconds, updated_at,
                    )
                    if status.endswith(" 1"):
                        settings_imported += 1

                for legacy_id, chat_id, user_id, text, created_at in message_rows:
                    status = await connection.execute(
                        """
                        INSERT INTO entertainment_messages(
                            chat_id, topic_id, message_id, user_id, text, created_at, legacy_source_id
                        ) VALUES($1, 0, NULL, $2, $3, $4, $5)
                        ON CONFLICT DO NOTHING
                        """,
                        chat_id, user_id, text, created_at, legacy_id,
                    )
                    if status.endswith(" 1"):
                        messages_imported += 1

                await connection.execute(
                    "INSERT INTO ent_schema_migrations(migration_key, applied_at) VALUES($1, $2)",
                    migration_key, int(time.time()),
                )
        return settings_imported, messages_imported

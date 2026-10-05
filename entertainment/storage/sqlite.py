"""Topic-aware SQLite entertainment storage."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import aiosqlite

from ..config import GENERATION_SAMPLE_LIMIT, MEMORY_LIMIT
from ..context import ActivitySnapshot
from ..models import (
    EntertainmentActionRecord,
    EntertainmentActionType,
    EntertainmentSettings,
    normalize_behavior_mode,
)

LOGGER = logging.getLogger(__name__)


def _normalize_optional_hour(value: object) -> int | None:
    if value is None:
        return None
    try:
        hour = int(value)
    except (TypeError, ValueError):
        return None
    return hour if 0 <= hour <= 23 else None


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
                behavior_mode TEXT NOT NULL DEFAULT 'alive',
                quiet_hours_start INTEGER,
                quiet_hours_end INTEGER,
                timezone TEXT NOT NULL DEFAULT 'Europe/Moscow',
                autonomous_text_enabled INTEGER NOT NULL DEFAULT 1,
                updated_at INTEGER NOT NULL
            )
            """
        )
        await self._ensure_settings_schema()
        await self._ensure_messages_schema()
        await self._ensure_actions_schema()
        await self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS ent_schema_migrations (
                migration_key TEXT PRIMARY KEY,
                applied_at INTEGER NOT NULL
            )
            """
        )
        await self.connection.commit()

    async def _ensure_settings_schema(self) -> None:
        connection = self._require_connection()
        async with connection.execute("PRAGMA table_info(entertainment_chat_settings)") as cursor:
            rows = await cursor.fetchall()
        columns = {str(row[1]) for row in rows}
        upgrades = (
            ("behavior_mode", "TEXT NOT NULL DEFAULT 'alive'"),
            ("quiet_hours_start", "INTEGER"),
            ("quiet_hours_end", "INTEGER"),
            ("timezone", "TEXT NOT NULL DEFAULT 'Europe/Moscow'"),
            ("autonomous_text_enabled", "INTEGER NOT NULL DEFAULT 1"),
        )
        for column, definition in upgrades:
            if column not in columns:
                await connection.execute(
                    f"ALTER TABLE entertainment_chat_settings ADD COLUMN {column} {definition}"
                )

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
                    created_at INTEGER NOT NULL,
                    legacy_source_id INTEGER
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
            if "legacy_source_id" not in columns:
                await connection.execute(
                    "ALTER TABLE entertainment_messages ADD COLUMN legacy_source_id INTEGER"
                )

        await connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_entertainment_messages_scope "
            "ON entertainment_messages(chat_id, topic_id, id DESC)"
        )
        await connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_entertainment_messages_activity "
            "ON entertainment_messages(chat_id, topic_id, created_at DESC)"
        )
        await connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_entertainment_messages_legacy_source "
            "ON entertainment_messages(legacy_source_id) WHERE legacy_source_id IS NOT NULL"
        )

    async def _ensure_actions_schema(self) -> None:
        connection = self._require_connection()
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS ent_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                topic_id INTEGER NOT NULL DEFAULT 0,
                action_type TEXT NOT NULL,
                trigger_message_id INTEGER,
                created_at INTEGER NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        await connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_ent_actions_scope_time "
            "ON ent_actions(chat_id, topic_id, created_at DESC, id DESC)"
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
            SELECT enabled, behavior_mode, quiet_hours_start, quiet_hours_end,
                   timezone, autonomous_text_enabled, laziness, cooldown_seconds
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
            behavior_mode=normalize_behavior_mode(row[1]),
            quiet_hours_start=_normalize_optional_hour(row[2]),
            quiet_hours_end=_normalize_optional_hour(row[3]),
            timezone=str(row[4] or "Europe/Moscow"),
            autonomous_text_enabled=bool(row[5]),
            laziness=max(0, min(100, int(row[6]))),
            cooldown_seconds=max(5, int(row[7])),
        )

    async def save_settings(self, chat_id: int, settings: EntertainmentSettings) -> None:
        connection = self._require_connection()
        behavior_mode = normalize_behavior_mode(settings.behavior_mode)
        await connection.execute(
            """
            INSERT INTO entertainment_chat_settings(
                chat_id, enabled, laziness, cooldown_seconds,
                behavior_mode, quiet_hours_start, quiet_hours_end,
                timezone, autonomous_text_enabled, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                enabled = excluded.enabled,
                laziness = excluded.laziness,
                cooldown_seconds = excluded.cooldown_seconds,
                behavior_mode = excluded.behavior_mode,
                quiet_hours_start = excluded.quiet_hours_start,
                quiet_hours_end = excluded.quiet_hours_end,
                timezone = excluded.timezone,
                autonomous_text_enabled = excluded.autonomous_text_enabled,
                updated_at = excluded.updated_at
            """,
            (
                int(chat_id),
                1 if settings.enabled else 0,
                max(0, min(100, int(settings.laziness))),
                max(5, int(settings.cooldown_seconds)),
                behavior_mode.value,
                _normalize_optional_hour(settings.quiet_hours_start),
                _normalize_optional_hour(settings.quiet_hours_end),
                str(settings.timezone or "Europe/Moscow"),
                1 if settings.autonomous_text_enabled else 0,
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
            SELECT text FROM entertainment_messages
            WHERE chat_id = ? AND topic_id = ?
            ORDER BY id DESC LIMIT ?
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

    async def activity_snapshot(
        self,
        chat_id: int,
        topic_id: int,
        *,
        now: int,
    ) -> ActivitySnapshot:
        connection = self._require_connection()
        now_i = int(now)
        async with connection.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN created_at >= ? AND created_at < ? THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END), 0),
                COUNT(DISTINCT CASE WHEN created_at >= ? THEN user_id END),
                COALESCE(SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END), 0),
                COUNT(*),
                COUNT(DISTINCT CASE WHEN created_at >= ? THEN user_id END),
                (
                    SELECT created_at
                    FROM entertainment_messages latest
                    WHERE latest.chat_id = ? AND latest.topic_id = ? AND latest.created_at <= ?
                    ORDER BY latest.created_at DESC
                    LIMIT 1
                )
            FROM entertainment_messages
            WHERE chat_id = ? AND topic_id = ?
              AND created_at >= ? AND created_at <= ?
            """,
            (
                now_i - 60,
                now_i - 300,
                now_i - 600,
                now_i - 300,
                now_i - 900,
                now_i - 300,
                now_i - 3_600,
                now_i - 3_600,
                int(chat_id),
                int(topic_id),
                now_i,
                int(chat_id),
                int(topic_id),
                now_i - 7_200,
                now_i,
            ),
        ) as cursor:
            row = await cursor.fetchone()
        assert row is not None
        last_human_at = row[8]
        return ActivitySnapshot(
            chat_id=int(chat_id),
            topic_id=int(topic_id),
            messages_1m=int(row[0]),
            messages_5m=int(row[1]),
            messages_previous_5m=int(row[2]),
            messages_15m=int(row[3]),
            active_users_5m=int(row[4]),
            seconds_since_human=(
                float(max(0, now_i - int(last_human_at))) if last_human_at is not None else None
            ),
            messages_60m=int(row[5]),
            messages_120m=int(row[6]),
            active_users_60m=int(row[7]),
        )

    async def record_action(self, record: EntertainmentActionRecord) -> int:
        connection = self._require_connection()
        cursor = await connection.execute(
            """
            INSERT INTO ent_actions(
                chat_id, topic_id, action_type, trigger_message_id, created_at, metadata_json
            ) VALUES(?, ?, ?, ?, ?, ?)
            """,
            (
                int(record.chat_id),
                int(record.topic_id),
                record.action_type.value,
                int(record.trigger_message_id) if record.trigger_message_id is not None else None,
                int(record.created_at),
                json.dumps(record.metadata, ensure_ascii=False, separators=(",", ":")),
            ),
        )
        await connection.commit()
        if cursor.lastrowid is None:
            raise RuntimeError("SQLite did not return an action id")
        return int(cursor.lastrowid)

    async def recent_actions(
        self,
        chat_id: int,
        topic_id: int,
        *,
        since: int,
        limit: int = 20,
    ) -> list[EntertainmentActionRecord]:
        connection = self._require_connection()
        async with connection.execute(
            """
            SELECT id, action_type, trigger_message_id, created_at, metadata_json
            FROM ent_actions
            WHERE chat_id = ? AND topic_id = ? AND created_at >= ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (int(chat_id), int(topic_id), int(since), max(1, int(limit))),
        ) as cursor:
            rows = await cursor.fetchall()

        actions: list[EntertainmentActionRecord] = []
        for row in rows:
            try:
                action_type = EntertainmentActionType(str(row[1]))
            except ValueError:
                LOGGER.warning("Skipping unknown entertainment action type id=%s type=%r", row[0], row[1])
                continue
            try:
                metadata = json.loads(str(row[4] or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                metadata = {}
            if not isinstance(metadata, dict):
                metadata = {}
            actions.append(
                EntertainmentActionRecord(
                    id=int(row[0]),
                    chat_id=int(chat_id),
                    topic_id=int(topic_id),
                    action_type=action_type,
                    trigger_message_id=(int(row[2]) if row[2] is not None else None),
                    created_at=int(row[3]),
                    metadata=metadata,
                )
            )
        return actions

    async def human_messages_since(self, chat_id: int, topic_id: int, *, since: int) -> int:
        connection = self._require_connection()
        async with connection.execute(
            """
            SELECT COUNT(*) FROM entertainment_messages
            WHERE chat_id = ? AND topic_id = ? AND created_at >= ?
            """,
            (int(chat_id), int(topic_id), int(since)),
        ) as cursor:
            row = await cursor.fetchone()
        return int(row[0]) if row else 0

    async def is_migration_applied(self, migration_key: str) -> bool:
        connection = self._require_connection()
        async with connection.execute(
            "SELECT 1 FROM ent_schema_migrations WHERE migration_key = ?",
            (migration_key,),
        ) as cursor:
            return await cursor.fetchone() is not None

    async def import_legacy_batch(
        self,
        migration_key: str,
        settings_rows: list[tuple[int, bool, int, int, int]],
        message_rows: list[tuple[int, int, int, str, int]],
    ) -> tuple[int, int]:
        connection = self._require_connection()
        settings_imported = 0
        messages_imported = 0
        await connection.execute("BEGIN IMMEDIATE")
        try:
            async with connection.execute(
                "SELECT 1 FROM ent_schema_migrations WHERE migration_key = ?",
                (migration_key,),
            ) as cursor:
                if await cursor.fetchone() is not None:
                    await connection.rollback()
                    return 0, 0

            for chat_id, enabled, laziness, cooldown_seconds, updated_at in settings_rows:
                cursor = await connection.execute(
                    """
                    INSERT OR IGNORE INTO entertainment_chat_settings(
                        chat_id, enabled, laziness, cooldown_seconds, updated_at
                    ) VALUES(?, ?, ?, ?, ?)
                    """,
                    (chat_id, 1 if enabled else 0, laziness, cooldown_seconds, updated_at),
                )
                settings_imported += max(0, cursor.rowcount)

            for legacy_id, chat_id, user_id, text, created_at in message_rows:
                cursor = await connection.execute(
                    """
                    INSERT OR IGNORE INTO entertainment_messages(
                        chat_id, topic_id, message_id, user_id, text, created_at, legacy_source_id
                    ) VALUES(?, 0, NULL, ?, ?, ?, ?)
                    """,
                    (chat_id, user_id, text, created_at, legacy_id),
                )
                messages_imported += max(0, cursor.rowcount)

            await connection.execute(
                "INSERT INTO ent_schema_migrations(migration_key, applied_at) VALUES(?, ?)",
                (migration_key, int(time.time())),
            )
            await connection.commit()
            return settings_imported, messages_imported
        except Exception:
            await connection.rollback()
            raise
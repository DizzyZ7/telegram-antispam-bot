"""Bounded, auditable writers-chat moderation history.

Only confirmed *deleted* prohibited-language messages are counted. The table
holds numeric Telegram ids and timestamps, never text, nicknames or inferred
personal attributes. Evidence ages out after 30 days and only the last 7
days affect repeat-warning wording. It does not trigger automatic punishments.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

RETENTION_DAYS = 30
REPEAT_WINDOW_DAYS = 7


@dataclass(frozen=True, slots=True)
class ModerationHistory:
    recent_count: int
    newly_recorded: bool


class WritersModerationLedger:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    async def _open(self) -> aiosqlite.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = await aiosqlite.connect(self.path, timeout=5.0)
        await db.execute("PRAGMA busy_timeout=5000")
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS writers_guard_violations (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                occurred_at INTEGER NOT NULL,
                reason TEXT NOT NULL,
                PRIMARY KEY(chat_id, message_id)
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_writers_guard_recent "
            "ON writers_guard_violations(chat_id,user_id,occurred_at)"
        )
        await db.commit()
        return db

    async def record_deleted_message(
        self,
        *,
        chat_id: int,
        user_id: int,
        message_id: int,
        at: int | None = None,
    ) -> ModerationHistory:
        when = int(time.time()) if at is None else int(at)
        db = await self._open()
        try:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                "DELETE FROM writers_guard_violations WHERE occurred_at < ?",
                (when - RETENTION_DAYS * 86400,),
            )
            cursor = await db.execute(
                """
                INSERT OR IGNORE INTO writers_guard_violations(
                    chat_id,user_id,message_id,occurred_at,reason
                ) VALUES (?,?,?,?,?)
                """,
                (int(chat_id), int(user_id), int(message_id), when, "prohibited_language"),
            )
            recorded = cursor.rowcount == 1
            async with db.execute(
                "SELECT COUNT(*) FROM writers_guard_violations "
                "WHERE chat_id=? AND user_id=? AND occurred_at>=? "
                "AND occurred_at<=?",
                (int(chat_id), int(user_id), when - REPEAT_WINDOW_DAYS * 86400, when),
            ) as query:
                row = await query.fetchone()
            await db.commit()
            return ModerationHistory(
                recent_count=int(row[0]) if row is not None else 0,
                newly_recorded=recorded,
            )
        except Exception:
            await db.rollback()
            raise
        finally:
            await db.close()

    async def recent_count(self, chat_id: int, user_id: int, *, at: int | None = None) -> int:
        now = int(time.time()) if at is None else int(at)
        db = await self._open()
        try:
            async with db.execute(
                "SELECT COUNT(*) FROM writers_guard_violations "
                "WHERE chat_id=? AND user_id=? AND occurred_at BETWEEN ? AND ?",
                (int(chat_id), int(user_id), now - REPEAT_WINDOW_DAYS * 86400, now),
            ) as cursor:
                row = await cursor.fetchone()
            return int(row[0]) if row is not None else 0
        finally:
            await db.close()

    async def clear_user(self, chat_id: int, user_id: int) -> int:
        db = await self._open()
        try:
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                "DELETE FROM writers_guard_violations WHERE chat_id=? AND user_id=?",
                (int(chat_id), int(user_id)),
            )
            await db.commit()
            return int(cursor.rowcount)
        except Exception:
            await db.rollback()
            raise
        finally:
            await db.close()


__all__ = [
    "ModerationHistory", "WritersModerationLedger",
    "RETENTION_DAYS", "REPEAT_WINDOW_DAYS",
]

"""Private, rate-limited human-review queue for writers-chat reports.

Reports are *allegations*, never strikes, violations or deletion commands.
No target-message text is stored, copied to moderators or sent to a model.
Records are automatically pruned after 30 days.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

REPORT_TTL_SECONDS = 30 * 86400
REPORT_RATE_WINDOW_SECONDS = 24 * 3600
MAX_REPORTS_PER_DAY = 3
VALID_REPORT_REASONS = frozenset({"spam", "insult", "harassment", "other"})
_DECISIONS = frozenset({"confirmed", "dismissed"})


class ReportRateLimited(Exception):
    """Too many submitted reports in one day."""


@dataclass(frozen=True, slots=True)
class ChatReport:
    id: int
    chat_id: int
    topic_id: int
    target_message_id: int
    target_user_id: int
    reporter_user_id: int
    reason: str
    status: str
    created_at: int


@dataclass(frozen=True, slots=True)
class ReportSubmission:
    report_id: int
    created: bool


class WritersReportQueue:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    async def _open(self) -> aiosqlite.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = await aiosqlite.connect(self.path, timeout=5)
        await db.execute("PRAGMA busy_timeout=5000")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS writers_guard_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                topic_id INTEGER NOT NULL,
                target_message_id INTEGER NOT NULL,
                target_user_id INTEGER NOT NULL,
                reporter_user_id INTEGER NOT NULL,
                reason TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at INTEGER NOT NULL,
                resolved_at INTEGER,
                resolved_by INTEGER,
                UNIQUE(chat_id, target_message_id, reporter_user_id)
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_writers_reports_pending "
            "ON writers_guard_reports(chat_id, status, created_at)"
        )
        await db.commit()
        return db

    async def submit(
        self, *, chat_id: int, topic_id: int, target_message_id: int,
        target_user_id: int, reporter_user_id: int, reason: str = "other",
        at: int | None = None,
    ) -> ReportSubmission:
        if reason not in VALID_REPORT_REASONS:
            raise ValueError("Report reason not supported")
        if reporter_user_id == target_user_id:
            raise ValueError("Self-report is not supported")
        now = int(time.time()) if at is None else int(at)
        db = await self._open()
        try:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                "DELETE FROM writers_guard_reports WHERE created_at < ?",
                (now - REPORT_TTL_SECONDS,),
            )
            async with db.execute(
                "SELECT id FROM writers_guard_reports "
                "WHERE chat_id=? AND target_message_id=? AND reporter_user_id=?",
                (chat_id, target_message_id, reporter_user_id),
            ) as cursor:
                existing = await cursor.fetchone()
            if existing is not None:
                await db.commit()
                return ReportSubmission(int(existing[0]), False)
            async with db.execute(
                "SELECT COUNT(*) FROM writers_guard_reports "
                "WHERE chat_id=? AND reporter_user_id=? "
                "AND created_at >= ? AND created_at <= ?",
                (chat_id, reporter_user_id, now - REPORT_RATE_WINDOW_SECONDS, now),
            ) as cursor:
                row = await cursor.fetchone()
            if int(row[0]) >= MAX_REPORTS_PER_DAY:
                await db.rollback()
                raise ReportRateLimited()
            cursor = await db.execute(
                "INSERT INTO writers_guard_reports("
                "chat_id,topic_id,target_message_id,target_user_id,"
                "reporter_user_id,reason,status,created_at)"
                "VALUES(?,?,?,?,?,?,'pending',?)",
                (chat_id, topic_id, target_message_id, target_user_id,
                 reporter_user_id, reason, now),
            )
            report_id = int(cursor.lastrowid)
            await db.commit()
            return ReportSubmission(report_id, True)
        except Exception:
            await db.rollback()
            raise
        finally:
            await db.close()

    async def pending(self, chat_id: int, *, limit: int = 10,
                      at: int | None = None) -> tuple[list[ChatReport], int]:
        now = int(time.time()) if at is None else int(at)
        db = await self._open()
        try:
            # Retention applies on reads too, not only on new submissions.
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                "DELETE FROM writers_guard_reports WHERE created_at < ?",
                (now - REPORT_TTL_SECONDS,),
            )
            async with db.execute(
                "SELECT COUNT(*) FROM writers_guard_reports "
                "WHERE chat_id=? AND status='pending'", (chat_id,),
            ) as cursor:
                count = int((await cursor.fetchone())[0])
            async with db.execute(
                "SELECT id,chat_id,topic_id,target_message_id,target_user_id,"
                "reporter_user_id,reason,status,created_at "
                "FROM writers_guard_reports WHERE chat_id=? AND status='pending' "
                "ORDER BY created_at ASC,id ASC LIMIT ?",
                (chat_id, max(1, min(int(limit), 20))),
            ) as cursor:
                rows = await cursor.fetchall()
            await db.commit()
            return [ChatReport(*row) for row in rows], count
        except Exception:
            await db.rollback()
            raise
        finally:
            await db.close()

    async def decide(
        self, *, chat_id: int, report_id: int, admin_user_id: int,
        decision: str, at: int | None = None,
    ) -> bool:
        if decision not in _DECISIONS:
            raise ValueError("Invalid decision")
        now = int(time.time()) if at is None else int(at)
        db = await self._open()
        try:
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                "UPDATE writers_guard_reports "
                "SET status=?,resolved_at=?,resolved_by=? "
                "WHERE id=? AND chat_id=? AND status='pending' "
                "AND created_at >= ?",
                (decision, now, admin_user_id, report_id, chat_id,
                 now - REPORT_TTL_SECONDS),
            )
            changed = cursor.rowcount == 1
            await db.commit()
            return changed
        except Exception:
            await db.rollback()
            raise
        finally:
            await db.close()


__all__ = [
    "ChatReport", "ReportSubmission", "ReportRateLimited",
    "WritersReportQueue", "VALID_REPORT_REASONS",
    "MAX_REPORTS_PER_DAY", "REPORT_TTL_SECONDS",
]

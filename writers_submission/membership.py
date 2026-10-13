"""Known ICФ community members, sourced only from Telegram observations.

Bot API cannot enumerate a channel's subscriber list. This registry collects
verified getChatMember checks and real chat_member updates. Its contents are
informational; publishing still requires a fresh Telegram membership check.
Only numeric Telegram identifiers, current status and timestamps are stored.
"""
from __future__ import annotations

import time
from typing import Any

NOTICE_INTERVAL_SECONDS = 24 * 3600


def is_current_member(member: Any) -> bool:
    raw = getattr(member, "status", "")
    status = str(getattr(raw, "value", raw)).casefold()
    if status in {"member", "administrator", "creator", "owner"}:
        return True
    if status == "restricted":
        return bool(getattr(member, "is_member", False))
    return False


class WritersCommunityMembershipRegistry:
    def __init__(self, submission_storage: Any) -> None:
        self.storage = submission_storage

    async def initialize(self) -> None:
        pool = self.storage._require_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS writers_community_members (
                    chat_id BIGINT NOT NULL,
                    user_id BIGINT NOT NULL,
                    status TEXT NOT NULL,
                    is_member BOOLEAN NOT NULL,
                    source TEXT NOT NULL,
                    observed_at BIGINT NOT NULL,
                    join_notice_at BIGINT,
                    PRIMARY KEY (chat_id, user_id)
                )
                """
            )
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_writers_community_members_current
                ON writers_community_members(chat_id, is_member)
                """
            )

    async def observe(
        self, *, chat_id: int, user_id: int, member: Any,
        source: str, at: int | None = None,
    ) -> None:
        if source not in {"lookup", "event"}:
            raise ValueError("Unrecognized membership source")
        timestamp = int(time.time() if at is None else at)
        raw = getattr(member, "status", "")
        status = str(getattr(raw, "value", raw)).casefold()
        current = is_current_member(member)
        pool = self.storage._require_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO writers_community_members(
                    chat_id,user_id,status,is_member,source,observed_at
                ) VALUES($1,$2,$3,$4,$5,$6)
                ON CONFLICT(chat_id,user_id) DO UPDATE
                SET status=EXCLUDED.status,
                    is_member=EXCLUDED.is_member,
                    source=EXCLUDED.source,
                    observed_at=EXCLUDED.observed_at
                WHERE EXCLUDED.observed_at >= writers_community_members.observed_at
                """,
                int(chat_id), int(user_id), status,
                current, source, timestamp,
            )

    async def claim_join_notice(
        self, chat_id: int, user_id: int, *, at: int | None = None,
    ) -> bool:
        timestamp = int(time.time() if at is None else at)
        pool = self.storage._require_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE writers_community_members
                SET join_notice_at=$3
                WHERE chat_id=$1 AND user_id=$2 AND NOT is_member
                    AND (join_notice_at IS NULL OR join_notice_at < $4)
                RETURNING user_id
                """,
                int(chat_id), int(user_id), timestamp,
                timestamp - NOTICE_INTERVAL_SECONDS,
            )
        return row is not None

    async def counts(self, chat_id: int) -> tuple[int, int]:
        """Known current vs known departed accounts; not full Telegram count."""
        pool = self.storage._require_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    COUNT(*) FILTER (WHERE is_member) AS known_current,
                    COUNT(*) FILTER (WHERE NOT is_member) AS known_departed
                FROM writers_community_members WHERE chat_id=$1
                """, int(chat_id),
            )
        return int(row["known_current"]), int(row["known_departed"])

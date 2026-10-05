from __future__ import annotations

import asyncpg

from .models import ChallengeRecord, ChallengeStatus

_ACTIVE_STATUSES = (ChallengeStatus.PENDING.value, ChallengeStatus.VERIFIED.value)


def _record_from_row(row: asyncpg.Record) -> ChallengeRecord:
    return ChallengeRecord(
        id=int(row["id"]),
        chat_id=int(row["chat_id"]),
        user_id=int(row["user_id"]),
        expected_answer=int(row["expected_answer"]),
        attempts=int(row["attempts"]),
        status=ChallengeStatus(str(row["status"])),
        created_at=int(row["created_at"]),
        expires_at=int(row["expires_at"]),
        verified_at=int(row["verified_at"]) if row["verified_at"] is not None else None,
        completed_at=int(row["completed_at"]) if row["completed_at"] is not None else None,
        telegram_message_id=(
            int(row["telegram_message_id"])
            if row["telegram_message_id"] is not None
            else None
        ),
        username=str(row["username"]) if row["username"] is not None else None,
        display_name=(
            str(row["display_name"]) if row["display_name"] is not None else None
        ),
    )


class PostgresZeroTrustStorage:
    def __init__(
        self,
        database_url: str,
        *,
        min_pool_size: int = 1,
        max_pool_size: int = 2,
    ) -> None:
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
                    CREATE TABLE IF NOT EXISTS zero_trust_challenges (
                        id BIGSERIAL PRIMARY KEY,
                        chat_id BIGINT NOT NULL,
                        user_id BIGINT NOT NULL,
                        expected_answer INTEGER NOT NULL,
                        attempts INTEGER NOT NULL DEFAULT 0,
                        status TEXT NOT NULL,
                        created_at BIGINT NOT NULL,
                        expires_at BIGINT NOT NULL,
                        verified_at BIGINT,
                        completed_at BIGINT,
                        telegram_message_id BIGINT,
                        username TEXT,
                        display_name TEXT,
                        CONSTRAINT zero_trust_challenges_status_check
                            CHECK (status IN ('pending', 'verified', 'passed', 'expired', 'cancelled'))
                    )
                    """
                )
                await connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_zero_trust_chat_user_history
                    ON zero_trust_challenges(chat_id, user_id, created_at DESC, id DESC)
                    """
                )
                await connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_zero_trust_user_history
                    ON zero_trust_challenges(user_id, created_at DESC, id DESC)
                    """
                )
                await connection.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_zero_trust_one_active
                    ON zero_trust_challenges(chat_id, user_id)
                    WHERE status IN ('pending', 'verified')
                    """
                )

    async def close(self) -> None:
        if self.pool is None:
            return
        await self.pool.close()
        self.pool = None

    def _require_pool(self) -> asyncpg.Pool:
        if self.pool is None:
            raise RuntimeError("Zero Trust PostgreSQL storage is not initialized")
        return self.pool

    @staticmethod
    async def _lock_scope(
        connection: asyncpg.Connection,
        chat_id: int,
        user_id: int,
    ) -> None:
        await connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
            f"{int(chat_id)}:{int(user_id)}",
        )

    async def create_challenge(
        self,
        *,
        chat_id: int,
        user_id: int,
        expected_answer: int,
        created_at: int,
        expires_at: int,
        username: str | None,
        display_name: str | None,
    ) -> ChallengeRecord:
        pool = self._require_pool()
        chat_id = int(chat_id)
        user_id = int(user_id)
        created_at = int(created_at)
        expires_at = int(expires_at)
        async with pool.acquire() as connection:
            async with connection.transaction():
                await self._lock_scope(connection, chat_id, user_id)
                await connection.execute(
                    """
                    UPDATE zero_trust_challenges
                    SET status = 'cancelled', completed_at = $3
                    WHERE chat_id = $1 AND user_id = $2
                      AND status IN ('pending', 'verified')
                    """,
                    chat_id,
                    user_id,
                    created_at,
                )
                row = await connection.fetchrow(
                    """
                    INSERT INTO zero_trust_challenges(
                        chat_id, user_id, expected_answer, attempts, status,
                        created_at, expires_at, username, display_name
                    ) VALUES($1, $2, $3, 0, 'pending', $4, $5, $6, $7)
                    RETURNING *
                    """,
                    chat_id,
                    user_id,
                    int(expected_answer),
                    created_at,
                    expires_at,
                    username,
                    display_name,
                )
        assert row is not None
        return _record_from_row(row)

    async def attach_message_id(self, challenge_id: int, message_id: int) -> None:
        await self._require_pool().execute(
            """
            UPDATE zero_trust_challenges
            SET telegram_message_id = $2
            WHERE id = $1
            """,
            int(challenge_id),
            int(message_id),
        )

    async def get_challenge(self, challenge_id: int) -> ChallengeRecord | None:
        row = await self._require_pool().fetchrow(
            "SELECT * FROM zero_trust_challenges WHERE id = $1",
            int(challenge_id),
        )
        return _record_from_row(row) if row is not None else None

    async def apply_answer(
        self,
        *,
        challenge_id: int,
        chat_id: int,
        user_id: int,
        answer: int,
        now: int,
    ) -> ChallengeRecord:
        pool = self._require_pool()
        async with pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow(
                    "SELECT * FROM zero_trust_challenges WHERE id = $1 FOR UPDATE",
                    int(challenge_id),
                )
                if row is None:
                    raise LookupError(f"Unknown Zero Trust challenge: {challenge_id}")

                current = _record_from_row(row)
                if current.chat_id != int(chat_id) or current.user_id != int(user_id):
                    return current
                if current.status is ChallengeStatus.VERIFIED:
                    return current
                if current.status is not ChallengeStatus.PENDING:
                    return current

                now = int(now)
                if now >= current.expires_at:
                    updated = await connection.fetchrow(
                        """
                        UPDATE zero_trust_challenges
                        SET status = 'expired', completed_at = $2
                        WHERE id = $1
                        RETURNING *
                        """,
                        current.id,
                        now,
                    )
                elif int(answer) != current.expected_answer:
                    updated = await connection.fetchrow(
                        """
                        UPDATE zero_trust_challenges
                        SET attempts = attempts + 1
                        WHERE id = $1
                        RETURNING *
                        """,
                        current.id,
                    )
                else:
                    updated = await connection.fetchrow(
                        """
                        UPDATE zero_trust_challenges
                        SET status = 'verified', verified_at = $2
                        WHERE id = $1
                        RETURNING *
                        """,
                        current.id,
                        now,
                    )
        assert updated is not None
        return _record_from_row(updated)

    async def mark_passed(
        self,
        *,
        challenge_id: int,
        chat_id: int,
        user_id: int,
        now: int,
    ) -> ChallengeRecord:
        pool = self._require_pool()
        async with pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow(
                    "SELECT * FROM zero_trust_challenges WHERE id = $1 FOR UPDATE",
                    int(challenge_id),
                )
                if row is None:
                    raise LookupError(f"Unknown Zero Trust challenge: {challenge_id}")
                current = _record_from_row(row)
                if current.chat_id != int(chat_id) or current.user_id != int(user_id):
                    return current
                if current.status is ChallengeStatus.PASSED:
                    return current
                if current.status is not ChallengeStatus.VERIFIED:
                    return current
                updated = await connection.fetchrow(
                    """
                    UPDATE zero_trust_challenges
                    SET status = 'passed', completed_at = $2
                    WHERE id = $1
                    RETURNING *
                    """,
                    current.id,
                    int(now),
                )
        assert updated is not None
        return _record_from_row(updated)

    async def cancel_active(self, *, chat_id: int, user_id: int, now: int) -> int:
        result = await self._require_pool().execute(
            """
            UPDATE zero_trust_challenges
            SET status = 'cancelled', completed_at = $3
            WHERE chat_id = $1 AND user_id = $2
              AND status IN ('pending', 'verified')
            """,
            int(chat_id),
            int(user_id),
            int(now),
        )
        return int(result.rsplit(" ", 1)[-1])

    async def expire_stale(self, *, now: int) -> int:
        result = await self._require_pool().execute(
            """
            UPDATE zero_trust_challenges
            SET status = 'expired', completed_at = $1
            WHERE status = 'pending' AND expires_at <= $1
            """,
            int(now),
        )
        return int(result.rsplit(" ", 1)[-1])

    async def history_for(
        self,
        chat_id: int,
        user_id: int,
        *,
        limit: int = 20,
    ) -> list[ChallengeRecord]:
        rows = await self._require_pool().fetch(
            """
            SELECT * FROM zero_trust_challenges
            WHERE chat_id = $1 AND user_id = $2
            ORDER BY created_at DESC, id DESC
            LIMIT $3
            """,
            int(chat_id),
            int(user_id),
            max(1, int(limit)),
        )
        return [_record_from_row(row) for row in rows]

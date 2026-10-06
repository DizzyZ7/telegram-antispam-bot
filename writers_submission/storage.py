from __future__ import annotations

import json
from uuid import UUID, uuid4

import asyncpg

from .models import (
    ConflictError,
    NotFoundError,
    RevisionState,
    SubmissionBundle,
    SubmissionRevision,
    SubmissionStatus,
    SubmissionSummary,
)
from .uploads import NormalizedSubmissionFields


def _uuid(value: object | None) -> UUID | None:
    if value is None:
        return None
    return value if isinstance(value, UUID) else UUID(str(value))


def _revision_from_row(row: asyncpg.Record) -> SubmissionRevision:
    return SubmissionRevision(
        id=_uuid(row["revision_id"]),
        submission_id=_uuid(row["submission_id"]),
        revision_number=int(row["revision_number"]),
        state=RevisionState(str(row["revision_state"])),
        title=str(row["title"]),
        work_type=str(row["work_type"]),
        genre=str(row["genre"]),
        description=str(row["description"]),
        body_text=str(row["body_text"]),
        external_url=(
            str(row["external_url"]) if row["external_url"] is not None else None
        ),
        created_at=int(row["revision_created_at"]),
        updated_at=int(row["revision_updated_at"]),
        sealed_at=(
            int(row["sealed_at"]) if row["sealed_at"] is not None else None
        ),
    )


def _bundle_from_row(row: asyncpg.Record) -> SubmissionBundle:
    revision = _revision_from_row(row)
    return SubmissionBundle(
        id=_uuid(row["submission_id"]),
        writers_chat_id=int(row["writers_chat_id"]),
        author_user_id=int(row["author_user_id"]),
        status=SubmissionStatus(str(row["submission_status"])),
        current_draft_revision_id=_uuid(row["current_draft_revision_id"]),
        current_submitted_revision_id=_uuid(
            row["current_submitted_revision_id"]
        ),
        claimed_by_user_id=(
            int(row["claimed_by_user_id"])
            if row["claimed_by_user_id"] is not None
            else None
        ),
        claimed_at=(
            int(row["claimed_at"]) if row["claimed_at"] is not None else None
        ),
        created_at=int(row["submission_created_at"]),
        updated_at=int(row["submission_updated_at"]),
        version=int(row["version"]),
        revision=revision,
    )


_BUNDLE_SELECT = """
SELECT
    s.id AS submission_id,
    s.writers_chat_id,
    s.author_user_id,
    s.status AS submission_status,
    s.current_draft_revision_id,
    s.current_submitted_revision_id,
    s.claimed_by_user_id,
    s.claimed_at,
    s.created_at AS submission_created_at,
    s.updated_at AS submission_updated_at,
    s.version,
    r.id AS revision_id,
    r.revision_number,
    r.state AS revision_state,
    r.title,
    r.work_type,
    r.genre,
    r.description,
    r.body_text,
    r.external_url,
    r.created_at AS revision_created_at,
    r.updated_at AS revision_updated_at,
    r.sealed_at
FROM writers_submissions AS s
JOIN writers_submission_revisions AS r
  ON r.id = COALESCE(
      s.current_draft_revision_id,
      s.current_submitted_revision_id
  )
"""


class PostgresWritersSubmissionStorage:
    def __init__(
        self,
        database_url: str,
        *,
        min_pool_size: int = 1,
        max_pool_size: int = 3,
    ) -> None:
        self.database_url = str(database_url)
        self.min_pool_size = max(1, int(min_pool_size))
        self.max_pool_size = max(self.min_pool_size, int(max_pool_size))
        self.pool: asyncpg.Pool | None = None

    async def initialize(self) -> None:
        self.pool = await asyncpg.create_pool(
            dsn=self.database_url,
            min_size=self.min_pool_size,
            max_size=self.max_pool_size,
            command_timeout=15,
        )
        pool = self._require_pool()
        async with pool.acquire() as connection:
            async with connection.transaction():
                await self._initialize_schema(connection)

    async def _initialize_schema(self, connection: asyncpg.Connection) -> None:
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS writers_submission_schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        await connection.execute(
            """
            INSERT INTO writers_submission_schema_meta(key, value)
            VALUES('schema_version', '1')
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            """
        )
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS writers_submissions (
                id UUID PRIMARY KEY,
                writers_chat_id BIGINT NOT NULL,
                author_user_id BIGINT NOT NULL,
                status TEXT NOT NULL,
                current_draft_revision_id UUID,
                current_submitted_revision_id UUID,
                claimed_by_user_id BIGINT,
                claimed_at BIGINT,
                created_at BIGINT NOT NULL,
                updated_at BIGINT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                CONSTRAINT writers_submissions_status_check CHECK (
                    status IN (
                        'DRAFT',
                        'SUBMITTED',
                        'IN_REVIEW',
                        'APPROVED',
                        'CHANGES_REQUESTED',
                        'REJECTED',
                        'WITHDRAWN'
                    )
                ),
                CONSTRAINT writers_submissions_version_check CHECK (version > 0)
            )
            """
        )
        await connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_writers_submissions_author_updated
            ON writers_submissions(author_user_id, updated_at DESC, id)
            """
        )
        await connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_writers_submissions_queue
            ON writers_submissions(status, updated_at, id)
            WHERE status IN ('SUBMITTED', 'IN_REVIEW')
            """
        )
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS writers_submission_revisions (
                id UUID PRIMARY KEY,
                submission_id UUID NOT NULL
                    REFERENCES writers_submissions(id) ON DELETE CASCADE,
                revision_number INTEGER NOT NULL,
                state TEXT NOT NULL,
                title TEXT NOT NULL,
                work_type TEXT NOT NULL,
                genre TEXT NOT NULL,
                description TEXT NOT NULL,
                body_text TEXT NOT NULL,
                external_url TEXT,
                created_at BIGINT NOT NULL,
                updated_at BIGINT NOT NULL,
                sealed_at BIGINT,
                CONSTRAINT writers_submission_revision_state_check
                    CHECK (state IN ('DRAFT', 'SEALED')),
                CONSTRAINT writers_submission_revision_number_check
                    CHECK (revision_number > 0),
                CONSTRAINT writers_submission_revision_number_unique
                    UNIQUE(submission_id, revision_number)
            )
            """
        )
        await connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_writers_revision_submission
            ON writers_submission_revisions(
                submission_id,
                revision_number DESC
            )
            """
        )
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS writers_submission_files (
                id UUID PRIMARY KEY,
                submission_id UUID NOT NULL
                    REFERENCES writers_submissions(id) ON DELETE CASCADE,
                revision_id UUID NOT NULL
                    REFERENCES writers_submission_revisions(id) ON DELETE CASCADE,
                safe_filename TEXT NOT NULL,
                declared_mime TEXT NOT NULL,
                detected_file_class TEXT NOT NULL,
                byte_size BIGINT NOT NULL,
                sha256 TEXT NOT NULL,
                telegram_file_id TEXT NOT NULL,
                telegram_file_unique_id TEXT,
                storage_chat_id BIGINT,
                storage_message_id BIGINT,
                created_at BIGINT NOT NULL,
                CONSTRAINT writers_submission_file_class_check
                    CHECK (detected_file_class IN ('pdf', 'docx', 'txt')),
                CONSTRAINT writers_submission_file_size_check
                    CHECK (byte_size > 0)
            )
            """
        )
        await connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_writers_files_revision
            ON writers_submission_files(revision_id, created_at, id)
            """
        )
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS writers_submission_reviews (
                id UUID PRIMARY KEY,
                submission_id UUID NOT NULL
                    REFERENCES writers_submissions(id) ON DELETE CASCADE,
                revision_id UUID NOT NULL
                    REFERENCES writers_submission_revisions(id) ON DELETE CASCADE,
                reviewer_user_id BIGINT NOT NULL,
                action TEXT NOT NULL,
                comment TEXT,
                created_at BIGINT NOT NULL,
                CONSTRAINT writers_submission_review_action_check CHECK (
                    action IN (
                        'CLAIM',
                        'APPROVE',
                        'REQUEST_CHANGES',
                        'REJECT'
                    )
                )
            )
            """
        )
        await connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_writers_reviews_submission
            ON writers_submission_reviews(submission_id, created_at, id)
            """
        )
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS writers_submission_events (
                id BIGSERIAL PRIMARY KEY,
                submission_id UUID NOT NULL
                    REFERENCES writers_submissions(id) ON DELETE CASCADE,
                revision_id UUID
                    REFERENCES writers_submission_revisions(id) ON DELETE CASCADE,
                actor_user_id BIGINT,
                event_type TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at BIGINT NOT NULL
            )
            """
        )
        await connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_writers_events_submission
            ON writers_submission_events(submission_id, created_at, id)
            """
        )
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS writers_submission_outbox (
                id UUID PRIMARY KEY,
                submission_id UUID NOT NULL
                    REFERENCES writers_submissions(id) ON DELETE CASCADE,
                revision_id UUID
                    REFERENCES writers_submission_revisions(id) ON DELETE CASCADE,
                event_type TEXT NOT NULL,
                state TEXT NOT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                next_attempt_at BIGINT NOT NULL,
                lease_until BIGINT,
                worker_id TEXT,
                last_error_code TEXT,
                payload_json TEXT NOT NULL DEFAULT '{}',
                dedupe_key TEXT UNIQUE,
                created_at BIGINT NOT NULL,
                updated_at BIGINT NOT NULL,
                CONSTRAINT writers_submission_outbox_event_type_check CHECK (
                    event_type IN ('MODERATION_CARD', 'AUTHOR_NOTIFICATION')
                ),
                CONSTRAINT writers_submission_outbox_state_check CHECK (
                    state IN (
                        'PENDING',
                        'IN_FLIGHT',
                        'DELIVERED',
                        'RETRYABLE_FAILED',
                        'PERMANENT_FAILED'
                    )
                ),
                CONSTRAINT writers_submission_outbox_attempt_check
                    CHECK (attempt_count >= 0)
            )
            """
        )
        await connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_writers_outbox_due
            ON writers_submission_outbox(state, next_attempt_at, created_at)
            WHERE state IN ('PENDING', 'RETRYABLE_FAILED', 'IN_FLIGHT')
            """
        )
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS writers_submission_idempotency (
                actor_user_id BIGINT NOT NULL,
                operation TEXT NOT NULL,
                client_key TEXT NOT NULL,
                result_submission_id UUID,
                result_revision_id UUID,
                response_json TEXT,
                created_at BIGINT NOT NULL,
                expires_at BIGINT NOT NULL,
                PRIMARY KEY(actor_user_id, operation, client_key)
            )
            """
        )
        await connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_writers_idempotency_expiry
            ON writers_submission_idempotency(expires_at)
            """
        )

    async def close(self) -> None:
        if self.pool is None:
            return
        await self.pool.close()
        self.pool = None

    def _require_pool(self) -> asyncpg.Pool:
        if self.pool is None:
            raise RuntimeError("Writers Submission PostgreSQL storage is not initialized")
        return self.pool

    @staticmethod
    async def _lock_idempotency(
        connection: asyncpg.Connection,
        *,
        actor_user_id: int,
        operation: str,
        client_key: str,
    ) -> None:
        await connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
            f"{int(actor_user_id)}:{operation}:{client_key}",
        )

    @staticmethod
    async def _idempotent_result(
        connection: asyncpg.Connection,
        *,
        actor_user_id: int,
        operation: str,
        client_key: str,
        now: int,
    ) -> asyncpg.Record | None:
        row = await connection.fetchrow(
            """
            SELECT *
            FROM writers_submission_idempotency
            WHERE actor_user_id = $1
              AND operation = $2
              AND client_key = $3
            """,
            int(actor_user_id),
            operation,
            client_key,
        )
        if row is None:
            return None
        if int(row["expires_at"]) > int(now):
            return row
        await connection.execute(
            """
            DELETE FROM writers_submission_idempotency
            WHERE actor_user_id = $1
              AND operation = $2
              AND client_key = $3
            """,
            int(actor_user_id),
            operation,
            client_key,
        )
        return None

    @staticmethod
    async def _record_idempotency(
        connection: asyncpg.Connection,
        *,
        actor_user_id: int,
        operation: str,
        client_key: str,
        submission_id: UUID,
        revision_id: UUID | None,
        now: int,
    ) -> None:
        await connection.execute(
            """
            INSERT INTO writers_submission_idempotency(
                actor_user_id,
                operation,
                client_key,
                result_submission_id,
                result_revision_id,
                response_json,
                created_at,
                expires_at
            )
            VALUES($1, $2, $3, $4, $5, '{}', $6, $7)
            """,
            int(actor_user_id),
            operation,
            client_key,
            submission_id,
            revision_id,
            int(now),
            int(now) + 7 * 24 * 60 * 60,
        )

    @staticmethod
    async def _bundle_on_connection(
        connection: asyncpg.Connection,
        *,
        submission_id: UUID,
        author_user_id: int,
    ) -> SubmissionBundle | None:
        row = await connection.fetchrow(
            _BUNDLE_SELECT
            + """
            WHERE s.id = $1 AND s.author_user_id = $2
            """,
            submission_id,
            int(author_user_id),
        )
        return _bundle_from_row(row) if row is not None else None

    async def create_submission(
        self,
        *,
        author_user_id: int,
        writers_chat_id: int,
        fields: NormalizedSubmissionFields,
        now: int,
        idempotency_key: str | None,
    ) -> SubmissionBundle:
        pool = self._require_pool()
        now = int(now)
        author_user_id = int(author_user_id)
        writers_chat_id = int(writers_chat_id)
        client_key = (
            str(idempotency_key).strip()
            if idempotency_key is not None
            else None
        )
        if client_key == "":
            client_key = None

        async with pool.acquire() as connection:
            async with connection.transaction():
                if client_key is not None:
                    await self._lock_idempotency(
                        connection,
                        actor_user_id=author_user_id,
                        operation="create",
                        client_key=client_key,
                    )
                    existing = await self._idempotent_result(
                        connection,
                        actor_user_id=author_user_id,
                        operation="create",
                        client_key=client_key,
                        now=now,
                    )
                    if existing is not None:
                        bundle = await self._bundle_on_connection(
                            connection,
                            submission_id=_uuid(existing["result_submission_id"]),
                            author_user_id=author_user_id,
                        )
                        if bundle is None:
                            raise ConflictError(
                                "Idempotency record points to a missing submission"
                            )
                        return bundle

                submission_id = uuid4()
                revision_id = uuid4()
                await connection.execute(
                    """
                    INSERT INTO writers_submissions(
                        id,
                        writers_chat_id,
                        author_user_id,
                        status,
                        created_at,
                        updated_at,
                        version
                    )
                    VALUES($1, $2, $3, 'DRAFT', $4, $4, 1)
                    """,
                    submission_id,
                    writers_chat_id,
                    author_user_id,
                    now,
                )
                await connection.execute(
                    """
                    INSERT INTO writers_submission_revisions(
                        id,
                        submission_id,
                        revision_number,
                        state,
                        title,
                        work_type,
                        genre,
                        description,
                        body_text,
                        external_url,
                        created_at,
                        updated_at
                    )
                    VALUES(
                        $1, $2, 1, 'DRAFT',
                        $3, $4, $5, $6, $7, $8,
                        $9, $9
                    )
                    """,
                    revision_id,
                    submission_id,
                    fields.title,
                    fields.work_type,
                    fields.genre,
                    fields.description,
                    fields.body_text,
                    fields.external_url,
                    now,
                )
                await connection.execute(
                    """
                    UPDATE writers_submissions
                    SET current_draft_revision_id = $2
                    WHERE id = $1
                    """,
                    submission_id,
                    revision_id,
                )
                await connection.execute(
                    """
                    INSERT INTO writers_submission_events(
                        submission_id,
                        revision_id,
                        actor_user_id,
                        event_type,
                        metadata_json,
                        created_at
                    )
                    VALUES($1, $2, $3, 'DRAFT_CREATED', $4, $5)
                    """,
                    submission_id,
                    revision_id,
                    author_user_id,
                    json.dumps({"revision_number": 1}, separators=(",", ":")),
                    now,
                )
                if client_key is not None:
                    await self._record_idempotency(
                        connection,
                        actor_user_id=author_user_id,
                        operation="create",
                        client_key=client_key,
                        submission_id=submission_id,
                        revision_id=revision_id,
                        now=now,
                    )
                bundle = await self._bundle_on_connection(
                    connection,
                    submission_id=submission_id,
                    author_user_id=author_user_id,
                )
        assert bundle is not None
        return bundle

    async def list_for_author(
        self,
        author_user_id: int,
        limit: int = 100,
    ) -> list[SubmissionSummary]:
        limit = max(1, min(100, int(limit)))
        rows = await self._require_pool().fetch(
            _BUNDLE_SELECT
            + """
            WHERE s.author_user_id = $1
            ORDER BY s.updated_at DESC, s.id
            LIMIT $2
            """,
            int(author_user_id),
            limit,
        )
        return [
            SubmissionSummary(
                id=_uuid(row["submission_id"]),
                status=SubmissionStatus(str(row["submission_status"])),
                title=str(row["title"]),
                revision_number=int(row["revision_number"]),
                updated_at=int(row["submission_updated_at"]),
                version=int(row["version"]),
            )
            for row in rows
        ]

    async def get_for_author(
        self,
        submission_id: UUID,
        author_user_id: int,
    ) -> SubmissionBundle | None:
        row = await self._require_pool().fetchrow(
            _BUNDLE_SELECT
            + """
            WHERE s.id = $1 AND s.author_user_id = $2
            """,
            submission_id,
            int(author_user_id),
        )
        return _bundle_from_row(row) if row is not None else None

    async def update_draft(
        self,
        *,
        submission_id: UUID,
        author_user_id: int,
        expected_version: int,
        fields: NormalizedSubmissionFields,
        now: int,
    ) -> SubmissionBundle:
        pool = self._require_pool()
        now = int(now)
        async with pool.acquire() as connection:
            async with connection.transaction():
                submission = await connection.fetchrow(
                    """
                    SELECT *
                    FROM writers_submissions
                    WHERE id = $1 AND author_user_id = $2
                    FOR UPDATE
                    """,
                    submission_id,
                    int(author_user_id),
                )
                if submission is None:
                    raise NotFoundError("Submission was not found")
                if str(submission["status"]) != SubmissionStatus.DRAFT.value:
                    raise ConflictError("Submission is not an editable draft")
                if int(submission["version"]) != int(expected_version):
                    raise ConflictError("Submission version is stale")

                revision_id = _uuid(submission["current_draft_revision_id"])
                if revision_id is None:
                    raise ConflictError("Submission has no current draft revision")

                result = await connection.execute(
                    """
                    UPDATE writers_submission_revisions
                    SET
                        title = $2,
                        work_type = $3,
                        genre = $4,
                        description = $5,
                        body_text = $6,
                        external_url = $7,
                        updated_at = $8
                    WHERE id = $1 AND state = 'DRAFT'
                    """,
                    revision_id,
                    fields.title,
                    fields.work_type,
                    fields.genre,
                    fields.description,
                    fields.body_text,
                    fields.external_url,
                    now,
                )
                if result != "UPDATE 1":
                    raise ConflictError("Draft revision is sealed or unavailable")

                updated = await connection.execute(
                    """
                    UPDATE writers_submissions
                    SET updated_at = $4, version = version + 1
                    WHERE id = $1
                      AND author_user_id = $2
                      AND version = $3
                    """,
                    submission_id,
                    int(author_user_id),
                    int(expected_version),
                    now,
                )
                if updated != "UPDATE 1":
                    raise ConflictError("Submission version is stale")

                await connection.execute(
                    """
                    INSERT INTO writers_submission_events(
                        submission_id,
                        revision_id,
                        actor_user_id,
                        event_type,
                        metadata_json,
                        created_at
                    )
                    VALUES($1, $2, $3, 'DRAFT_UPDATED', '{}', $4)
                    """,
                    submission_id,
                    revision_id,
                    int(author_user_id),
                    now,
                )
                row = await connection.fetchrow(
                    _BUNDLE_SELECT
                    + """
                    WHERE s.id = $1 AND s.author_user_id = $2
                    """,
                    submission_id,
                    int(author_user_id),
                )
        assert row is not None
        return _bundle_from_row(row)


    async def withdraw_submission(
        self,
        *,
        submission_id: UUID,
        author_user_id: int,
        idempotency_key: str,
        now: int,
    ) -> SubmissionBundle:
        pool = self._require_pool()
        author_user_id = int(author_user_id)
        client_key = str(idempotency_key).strip()
        if not client_key:
            raise ValueError("idempotency_key is required")
        now = int(now)

        async with pool.acquire() as connection:
            async with connection.transaction():
                await self._lock_idempotency(
                    connection,
                    actor_user_id=author_user_id,
                    operation="withdraw",
                    client_key=client_key,
                )
                existing = await self._idempotent_result(
                    connection,
                    actor_user_id=author_user_id,
                    operation="withdraw",
                    client_key=client_key,
                    now=now,
                )
                if existing is not None:
                    bundle = await self._bundle_on_connection(
                        connection,
                        submission_id=_uuid(existing["result_submission_id"]),
                        author_user_id=author_user_id,
                    )
                    if bundle is None:
                        raise ConflictError(
                            "Idempotency record points to a missing submission"
                        )
                    return bundle

                row = await connection.fetchrow(
                    """
                    SELECT *
                    FROM writers_submissions
                    WHERE id = $1 AND author_user_id = $2
                    FOR UPDATE
                    """,
                    submission_id,
                    author_user_id,
                )
                if row is None:
                    raise NotFoundError("Submission was not found")
                status = SubmissionStatus(str(row["status"]))
                if status not in {
                    SubmissionStatus.DRAFT,
                    SubmissionStatus.SUBMITTED,
                    SubmissionStatus.IN_REVIEW,
                    SubmissionStatus.CHANGES_REQUESTED,
                }:
                    raise ConflictError("Submission cannot be withdrawn")

                revision_id = _uuid(
                    row["current_draft_revision_id"]
                    or row["current_submitted_revision_id"]
                )
                await connection.execute(
                    """
                    UPDATE writers_submissions
                    SET status = 'WITHDRAWN',
                        updated_at = $3,
                        version = version + 1
                    WHERE id = $1 AND author_user_id = $2
                    """,
                    submission_id,
                    author_user_id,
                    now,
                )
                await connection.execute(
                    """
                    INSERT INTO writers_submission_events(
                        submission_id,
                        revision_id,
                        actor_user_id,
                        event_type,
                        metadata_json,
                        created_at
                    )
                    VALUES($1, $2, $3, 'WITHDRAWN', '{}', $4)
                    """,
                    submission_id,
                    revision_id,
                    author_user_id,
                    now,
                )
                await self._record_idempotency(
                    connection,
                    actor_user_id=author_user_id,
                    operation="withdraw",
                    client_key=client_key,
                    submission_id=submission_id,
                    revision_id=revision_id,
                    now=now,
                )
                bundle = await self._bundle_on_connection(
                    connection,
                    submission_id=submission_id,
                    author_user_id=author_user_id,
                )
        assert bundle is not None
        return bundle

    async def create_revision(
        self,
        *,
        submission_id: UUID,
        author_user_id: int,
        idempotency_key: str,
        now: int,
    ) -> SubmissionBundle:
        pool = self._require_pool()
        author_user_id = int(author_user_id)
        client_key = str(idempotency_key).strip()
        if not client_key:
            raise ValueError("idempotency_key is required")
        now = int(now)

        async with pool.acquire() as connection:
            async with connection.transaction():
                await self._lock_idempotency(
                    connection,
                    actor_user_id=author_user_id,
                    operation="create_revision",
                    client_key=client_key,
                )
                existing = await self._idempotent_result(
                    connection,
                    actor_user_id=author_user_id,
                    operation="create_revision",
                    client_key=client_key,
                    now=now,
                )
                if existing is not None:
                    bundle = await self._bundle_on_connection(
                        connection,
                        submission_id=_uuid(existing["result_submission_id"]),
                        author_user_id=author_user_id,
                    )
                    if bundle is None:
                        raise ConflictError(
                            "Idempotency record points to a missing submission"
                        )
                    return bundle

                submission = await connection.fetchrow(
                    """
                    SELECT *
                    FROM writers_submissions
                    WHERE id = $1 AND author_user_id = $2
                    FOR UPDATE
                    """,
                    submission_id,
                    author_user_id,
                )
                if submission is None:
                    raise NotFoundError("Submission was not found")
                if (
                    str(submission["status"])
                    != SubmissionStatus.CHANGES_REQUESTED.value
                ):
                    raise ConflictError(
                        "A new revision requires CHANGES_REQUESTED state"
                    )
                source_revision_id = _uuid(
                    submission["current_submitted_revision_id"]
                )
                if source_revision_id is None:
                    raise ConflictError("Submission has no sealed revision")

                source = await connection.fetchrow(
                    """
                    SELECT *
                    FROM writers_submission_revisions
                    WHERE id = $1
                      AND submission_id = $2
                      AND state = 'SEALED'
                    """,
                    source_revision_id,
                    submission_id,
                )
                if source is None:
                    raise ConflictError("Submitted revision is unavailable")

                revision_id = uuid4()
                revision_number = int(source["revision_number"]) + 1
                await connection.execute(
                    """
                    INSERT INTO writers_submission_revisions(
                        id,
                        submission_id,
                        revision_number,
                        state,
                        title,
                        work_type,
                        genre,
                        description,
                        body_text,
                        external_url,
                        created_at,
                        updated_at
                    )
                    VALUES(
                        $1, $2, $3, 'DRAFT',
                        $4, $5, $6, $7, $8, $9,
                        $10, $10
                    )
                    """,
                    revision_id,
                    submission_id,
                    revision_number,
                    source["title"],
                    source["work_type"],
                    source["genre"],
                    source["description"],
                    source["body_text"],
                    source["external_url"],
                    now,
                )
                await connection.execute(
                    """
                    UPDATE writers_submissions
                    SET status = 'DRAFT',
                        current_draft_revision_id = $3,
                        claimed_by_user_id = NULL,
                        claimed_at = NULL,
                        updated_at = $4,
                        version = version + 1
                    WHERE id = $1 AND author_user_id = $2
                    """,
                    submission_id,
                    author_user_id,
                    revision_id,
                    now,
                )
                await connection.execute(
                    """
                    INSERT INTO writers_submission_events(
                        submission_id,
                        revision_id,
                        actor_user_id,
                        event_type,
                        metadata_json,
                        created_at
                    )
                    VALUES($1, $2, $3, 'REVISION_CREATED', $4, $5)
                    """,
                    submission_id,
                    revision_id,
                    author_user_id,
                    json.dumps(
                        {
                            "revision_number": revision_number,
                            "source_revision_id": str(source_revision_id),
                        },
                        separators=(",", ":"),
                    ),
                    now,
                )
                await self._record_idempotency(
                    connection,
                    actor_user_id=author_user_id,
                    operation="create_revision",
                    client_key=client_key,
                    submission_id=submission_id,
                    revision_id=revision_id,
                    now=now,
                )
                bundle = await self._bundle_on_connection(
                    connection,
                    submission_id=submission_id,
                    author_user_id=author_user_id,
                )
        assert bundle is not None
        return bundle

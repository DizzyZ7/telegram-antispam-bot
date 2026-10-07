from __future__ import annotations

import json
import secrets
from uuid import UUID, uuid4

import asyncpg

from .models import (
    AuthorNotificationContext,
    ConflictError,
    DraftFileContext,
    ModerationDeliveryContext,
    ModerationResult,
    ModerationTarget,
    NotFoundError,
    OutboxEventType,
    OutboxRecord,
    OutboxState,
    ReviewAction,
    RevisionState,
    SubmissionBundle,
    SubmissionFile,
    SubmissionRevision,
    SubmissionStatus,
    SubmissionSummary,
    ValidationError,
)
from .uploads import NormalizedSubmissionFields, ValidatedUpload


def _uuid(value: object | None) -> UUID | None:
    if value is None:
        return None
    return value if isinstance(value, UUID) else UUID(str(value))


def _outbox_from_row(row: asyncpg.Record) -> OutboxRecord:
    raw_payload = row["payload_json"]
    try:
        payload = json.loads(str(raw_payload or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return OutboxRecord(
        id=_uuid(row["id"]),
        submission_id=_uuid(row["submission_id"]),
        revision_id=_uuid(row["revision_id"]),
        event_type=OutboxEventType(str(row["event_type"])),
        state=OutboxState(str(row["state"])),
        attempt_count=int(row["attempt_count"]),
        next_attempt_at=int(row["next_attempt_at"]),
        lease_until=(
            int(row["lease_until"]) if row["lease_until"] is not None else None
        ),
        worker_id=str(row["worker_id"]) if row["worker_id"] is not None else None,
        last_error_code=(
            str(row["last_error_code"])
            if row["last_error_code"] is not None
            else None
        ),
        payload=payload,
        dedupe_key=(
            str(row["dedupe_key"]) if row["dedupe_key"] is not None else None
        ),
        created_at=int(row["created_at"]),
        updated_at=int(row["updated_at"]),
    )


def _file_from_row(row: asyncpg.Record) -> SubmissionFile:
    return SubmissionFile(
        id=_uuid(row["id"]),
        submission_id=_uuid(row["submission_id"]),
        revision_id=_uuid(row["revision_id"]),
        safe_filename=str(row["safe_filename"]),
        declared_mime=str(row["declared_mime"]),
        detected_file_class=str(row["detected_file_class"]),
        byte_size=int(row["byte_size"]),
        sha256=str(row["sha256"]),
        telegram_file_id=str(row["telegram_file_id"]),
        telegram_file_unique_id=(
            str(row["telegram_file_unique_id"])
            if row["telegram_file_unique_id"] is not None
            else None
        ),
        storage_chat_id=(
            int(row["storage_chat_id"])
            if row["storage_chat_id"] is not None
            else None
        ),
        storage_message_id=(
            int(row["storage_message_id"])
            if row["storage_message_id"] is not None
            else None
        ),
        created_at=int(row["created_at"]),
    )


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
            CREATE TABLE IF NOT EXISTS writers_submission_moderation_tokens (
                token TEXT PRIMARY KEY,
                submission_id UUID NOT NULL
                    REFERENCES writers_submissions(id) ON DELETE CASCADE,
                revision_id UUID NOT NULL
                    REFERENCES writers_submission_revisions(id) ON DELETE CASCADE,
                created_at BIGINT NOT NULL,
                CONSTRAINT writers_submission_moderation_target_unique
                    UNIQUE(submission_id, revision_id)
            )
            """
        )
        await connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_writers_moderation_tokens_target
            ON writers_submission_moderation_tokens(submission_id, revision_id)
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
                delivery_chat_id BIGINT,
                delivery_message_ids_json TEXT NOT NULL DEFAULT '[]',
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
            ALTER TABLE writers_submission_outbox
            ADD COLUMN IF NOT EXISTS delivery_chat_id BIGINT
            """
        )
        await connection.execute(
            """
            ALTER TABLE writers_submission_outbox
            ADD COLUMN IF NOT EXISTS delivery_message_ids_json TEXT NOT NULL DEFAULT '[]'
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
                if revision_id is not None:
                    await connection.execute(
                        """
                        INSERT INTO writers_submission_outbox(
                            id,
                            submission_id,
                            revision_id,
                            event_type,
                            state,
                            attempt_count,
                            next_attempt_at,
                            payload_json,
                            dedupe_key,
                            created_at,
                            updated_at
                        )
                        VALUES(
                            $1, $2, $3, 'AUTHOR_NOTIFICATION', 'PENDING',
                            0, $4, $5, $6, $4, $4
                        )
                        ON CONFLICT (dedupe_key) DO NOTHING
                        """,
                        uuid4(),
                        submission_id,
                        revision_id,
                        now,
                        json.dumps(
                            {"kind": "WITHDRAWN"},
                            separators=(",", ":"),
                        ),
                        f"author:{submission_id}:{revision_id}:WITHDRAWN",
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


    async def get_draft_file_context(
        self,
        *,
        submission_id: UUID,
        author_user_id: int,
    ) -> DraftFileContext:
        pool = self._require_pool()
        async with pool.acquire() as connection:
            submission = await connection.fetchrow(
                """
                SELECT status, current_draft_revision_id
                FROM writers_submissions
                WHERE id = $1 AND author_user_id = $2
                """,
                submission_id,
                int(author_user_id),
            )
            if submission is None:
                raise NotFoundError("Submission was not found")
            if str(submission["status"]) != SubmissionStatus.DRAFT.value:
                raise ConflictError("Submission is not an editable draft")
            revision_id = _uuid(submission["current_draft_revision_id"])
            if revision_id is None:
                raise ConflictError("Submission has no current draft revision")
            state = await connection.fetchval(
                """
                SELECT state
                FROM writers_submission_revisions
                WHERE id = $1 AND submission_id = $2
                """,
                revision_id,
                submission_id,
            )
            if state != RevisionState.DRAFT.value:
                raise ConflictError("Draft revision is sealed")
            count = await connection.fetchval(
                """
                SELECT COUNT(*)
                FROM writers_submission_files
                WHERE submission_id = $1 AND revision_id = $2
                """,
                submission_id,
                revision_id,
            )
        return DraftFileContext(
            revision_id=revision_id,
            file_count=int(count or 0),
        )

    async def add_ready_file(
        self,
        *,
        submission_id: UUID,
        author_user_id: int,
        revision_id: UUID,
        upload: ValidatedUpload,
        telegram_file_id: str,
        telegram_file_unique_id: str | None,
        storage_chat_id: int,
        storage_message_id: int,
        max_files: int,
        now: int,
    ) -> SubmissionFile:
        if int(max_files) <= 0:
            raise ValueError("max_files must be positive")
        pool = self._require_pool()
        file_id = uuid4()
        async with pool.acquire() as connection:
            async with connection.transaction():
                submission = await connection.fetchrow(
                    """
                    SELECT status, current_draft_revision_id
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
                current_revision_id = _uuid(submission["current_draft_revision_id"])
                if current_revision_id != revision_id:
                    raise ConflictError("Draft revision changed during upload")
                state = await connection.fetchval(
                    """
                    SELECT state
                    FROM writers_submission_revisions
                    WHERE id = $1 AND submission_id = $2
                    """,
                    revision_id,
                    submission_id,
                )
                if state != RevisionState.DRAFT.value:
                    raise ConflictError("Draft revision is sealed")

                count = await connection.fetchval(
                    """
                    SELECT COUNT(*)
                    FROM writers_submission_files
                    WHERE submission_id = $1 AND revision_id = $2
                    """,
                    submission_id,
                    revision_id,
                )
                if int(count or 0) >= int(max_files):
                    raise ValidationError("maximum file count reached")

                row = await connection.fetchrow(
                    """
                    INSERT INTO writers_submission_files(
                        id,
                        submission_id,
                        revision_id,
                        safe_filename,
                        declared_mime,
                        detected_file_class,
                        byte_size,
                        sha256,
                        telegram_file_id,
                        telegram_file_unique_id,
                        storage_chat_id,
                        storage_message_id,
                        created_at
                    )
                    VALUES(
                        $1, $2, $3, $4, $5, $6, $7, $8,
                        $9, $10, $11, $12, $13
                    )
                    RETURNING *
                    """,
                    file_id,
                    submission_id,
                    revision_id,
                    upload.safe_filename,
                    upload.declared_mime,
                    upload.file_class,
                    int(upload.byte_size),
                    upload.sha256,
                    str(telegram_file_id),
                    (
                        str(telegram_file_unique_id)
                        if telegram_file_unique_id is not None
                        else None
                    ),
                    int(storage_chat_id),
                    int(storage_message_id),
                    int(now),
                )
                await connection.execute(
                    """
                    UPDATE writers_submissions
                    SET updated_at = $3,
                        version = version + 1
                    WHERE id = $1 AND author_user_id = $2
                    """,
                    submission_id,
                    int(author_user_id),
                    int(now),
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
                    VALUES($1, $2, $3, 'FILE_ATTACHED', $4, $5)
                    """,
                    submission_id,
                    revision_id,
                    int(author_user_id),
                    json.dumps(
                        {
                            "file_id": str(file_id),
                            "file_class": upload.file_class,
                            "byte_size": int(upload.byte_size),
                        },
                        separators=(",", ":"),
                    ),
                    int(now),
                )
        assert row is not None
        return _file_from_row(row)

    async def delete_ready_file(
        self,
        *,
        submission_id: UUID,
        author_user_id: int,
        file_id: UUID,
        now: int,
    ) -> SubmissionFile:
        pool = self._require_pool()
        async with pool.acquire() as connection:
            async with connection.transaction():
                submission = await connection.fetchrow(
                    """
                    SELECT status, current_draft_revision_id
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
                revision_id = _uuid(submission["current_draft_revision_id"])
                if revision_id is None:
                    raise ConflictError("Submission has no current draft revision")
                state = await connection.fetchval(
                    """
                    SELECT state
                    FROM writers_submission_revisions
                    WHERE id = $1 AND submission_id = $2
                    """,
                    revision_id,
                    submission_id,
                )
                if state != RevisionState.DRAFT.value:
                    raise ConflictError("Draft revision is sealed")

                row = await connection.fetchrow(
                    """
                    DELETE FROM writers_submission_files
                    WHERE id = $1
                      AND submission_id = $2
                      AND revision_id = $3
                    RETURNING *
                    """,
                    file_id,
                    submission_id,
                    revision_id,
                )
                if row is None:
                    raise NotFoundError("Attachment was not found")
                await connection.execute(
                    """
                    UPDATE writers_submissions
                    SET updated_at = $3,
                        version = version + 1
                    WHERE id = $1 AND author_user_id = $2
                    """,
                    submission_id,
                    int(author_user_id),
                    int(now),
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
                    VALUES($1, $2, $3, 'FILE_DELETED', $4, $5)
                    """,
                    submission_id,
                    revision_id,
                    int(author_user_id),
                    json.dumps(
                        {"file_id": str(file_id)},
                        separators=(",", ":"),
                    ),
                    int(now),
                )
        return _file_from_row(row)

    async def list_files_for_author(
        self,
        *,
        submission_id: UUID,
        author_user_id: int,
    ) -> list[SubmissionFile]:
        owned = await self.get_for_author(submission_id, int(author_user_id))
        if owned is None:
            raise NotFoundError("Submission was not found")
        rows = await self._require_pool().fetch(
            """
            SELECT *
            FROM writers_submission_files
            WHERE submission_id = $1
            ORDER BY created_at, id
            """,
            submission_id,
        )
        return [_file_from_row(row) for row in rows]


    async def list_history_for_author(
        self,
        *,
        submission_id: UUID,
        author_user_id: int,
        limit: int = 200,
    ) -> list[dict[str, object]]:
        owned = await self.get_for_author(submission_id, int(author_user_id))
        if owned is None:
            raise NotFoundError("Submission was not found")

        rows = await self._require_pool().fetch(
            """
            SELECT id, revision_id, actor_user_id, event_type, metadata_json, created_at
            FROM writers_submission_events
            WHERE submission_id = $1
            ORDER BY created_at ASC, id ASC
            LIMIT $2
            """,
            submission_id,
            max(1, min(500, int(limit))),
        )
        result: list[dict[str, object]] = []
        for row in rows:
            metadata: object = {}
            try:
                parsed = json.loads(str(row["metadata_json"] or "{}"))
                if isinstance(parsed, dict):
                    metadata = parsed
            except (TypeError, ValueError, json.JSONDecodeError):
                metadata = {}
            result.append(
                {
                    "id": int(row["id"]),
                    "revision_id": (
                        str(row["revision_id"])
                        if row["revision_id"] is not None
                        else None
                    ),
                    "actor_user_id": (
                        int(row["actor_user_id"])
                        if row["actor_user_id"] is not None
                        else None
                    ),
                    "event_type": str(row["event_type"]),
                    "metadata": metadata,
                    "created_at": int(row["created_at"]),
                }
            )
        return result


    async def seal_and_submit(
        self,
        *,
        submission_id: UUID,
        author_user_id: int,
        expected_version: int,
        idempotency_key: str,
        now: int,
    ) -> SubmissionBundle:
        pool = self._require_pool()
        author_user_id = int(author_user_id)
        expected_version = int(expected_version)
        now = int(now)
        client_key = str(idempotency_key).strip()
        if not client_key:
            raise ValueError("idempotency_key is required")

        async with pool.acquire() as connection:
            async with connection.transaction():
                await self._lock_idempotency(
                    connection,
                    actor_user_id=author_user_id,
                    operation="submit",
                    client_key=client_key,
                )
                existing = await self._idempotent_result(
                    connection,
                    actor_user_id=author_user_id,
                    operation="submit",
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
                if str(submission["status"]) != SubmissionStatus.DRAFT.value:
                    raise ConflictError("Submission is not an editable draft")
                if int(submission["version"]) != expected_version:
                    raise ConflictError("Submission version is stale")

                revision_id = _uuid(submission["current_draft_revision_id"])
                if revision_id is None:
                    raise ConflictError("Submission has no current draft revision")
                revision = await connection.fetchrow(
                    """
                    SELECT *
                    FROM writers_submission_revisions
                    WHERE id = $1
                      AND submission_id = $2
                    FOR UPDATE
                    """,
                    revision_id,
                    submission_id,
                )
                if revision is None:
                    raise ConflictError("Draft revision is unavailable")
                if str(revision["state"]) != RevisionState.DRAFT.value:
                    raise ConflictError("Draft revision is already sealed")

                file_stats = await connection.fetchrow(
                    """
                    SELECT
                        COUNT(*) AS file_count,
                        COUNT(*) FILTER (
                            WHERE NULLIF(BTRIM(telegram_file_id), '') IS NULL
                        ) AS invalid_file_count
                    FROM writers_submission_files
                    WHERE submission_id = $1 AND revision_id = $2
                    """,
                    submission_id,
                    revision_id,
                )
                file_count = int(file_stats["file_count"] or 0)
                invalid_file_count = int(file_stats["invalid_file_count"] or 0)
                if invalid_file_count:
                    raise ValidationError("Submission has an incomplete attachment")
                if not str(revision["body_text"]).strip() and file_count == 0:
                    raise ValidationError(
                        "Submission requires body text or at least one ready file"
                    )

                sealed = await connection.execute(
                    """
                    UPDATE writers_submission_revisions
                    SET state = 'SEALED',
                        sealed_at = $3,
                        updated_at = $3
                    WHERE id = $1
                      AND submission_id = $2
                      AND state = 'DRAFT'
                    """,
                    revision_id,
                    submission_id,
                    now,
                )
                if sealed != "UPDATE 1":
                    raise ConflictError("Draft revision changed during submit")

                updated = await connection.execute(
                    """
                    UPDATE writers_submissions
                    SET status = 'SUBMITTED',
                        current_submitted_revision_id = $3,
                        current_draft_revision_id = NULL,
                        claimed_by_user_id = NULL,
                        claimed_at = NULL,
                        updated_at = $4,
                        version = version + 1
                    WHERE id = $1
                      AND author_user_id = $2
                      AND status = 'DRAFT'
                      AND version = $5
                    """,
                    submission_id,
                    author_user_id,
                    revision_id,
                    now,
                    expected_version,
                )
                if updated != "UPDATE 1":
                    raise ConflictError("Submission changed during submit")

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
                    VALUES($1, $2, $3, 'SUBMITTED', $4, $5)
                    """,
                    submission_id,
                    revision_id,
                    author_user_id,
                    json.dumps(
                        {"revision_number": int(revision["revision_number"])},
                        separators=(",", ":"),
                    ),
                    now,
                )

                outbox_id = uuid4()
                dedupe_key = f"moderation:{submission_id}:{revision_id}"
                await connection.execute(
                    """
                    INSERT INTO writers_submission_outbox(
                        id,
                        submission_id,
                        revision_id,
                        event_type,
                        state,
                        attempt_count,
                        next_attempt_at,
                        payload_json,
                        dedupe_key,
                        created_at,
                        updated_at
                    )
                    VALUES(
                        $1, $2, $3, 'MODERATION_CARD', 'PENDING',
                        0, $4, '{}', $5, $4, $4
                    )
                    ON CONFLICT (dedupe_key) DO NOTHING
                    """,
                    outbox_id,
                    submission_id,
                    revision_id,
                    now,
                    dedupe_key,
                )
                await connection.execute(
                    """
                    INSERT INTO writers_submission_outbox(
                        id,
                        submission_id,
                        revision_id,
                        event_type,
                        state,
                        attempt_count,
                        next_attempt_at,
                        payload_json,
                        dedupe_key,
                        created_at,
                        updated_at
                    )
                    VALUES(
                        $1, $2, $3, 'AUTHOR_NOTIFICATION', 'PENDING',
                        0, $4, $5, $6, $4, $4
                    )
                    ON CONFLICT (dedupe_key) DO NOTHING
                    """,
                    uuid4(),
                    submission_id,
                    revision_id,
                    now,
                    json.dumps(
                        {"kind": "SUBMISSION_ACCEPTED"},
                        separators=(",", ":"),
                    ),
                    f"author:{submission_id}:{revision_id}:SUBMISSION_ACCEPTED",
                )
                await self._record_idempotency(
                    connection,
                    actor_user_id=author_user_id,
                    operation="submit",
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

    async def get_or_create_moderation_token(
        self,
        *,
        submission_id: UUID,
        revision_id: UUID,
        now: int,
    ) -> str:
        pool = self._require_pool()
        async with pool.acquire() as connection:
            async with connection.transaction():
                target = await connection.fetchrow(
                    """
                    SELECT s.id
                    FROM writers_submissions AS s
                    JOIN writers_submission_revisions AS r
                      ON r.submission_id = s.id
                    WHERE s.id = $1 AND r.id = $2
                    FOR UPDATE OF s
                    """,
                    submission_id,
                    revision_id,
                )
                if target is None:
                    raise NotFoundError("Submission revision was not found")

                existing = await connection.fetchval(
                    """
                    SELECT token
                    FROM writers_submission_moderation_tokens
                    WHERE submission_id = $1 AND revision_id = $2
                    """,
                    submission_id,
                    revision_id,
                )
                if existing is not None:
                    return str(existing)

                for _ in range(8):
                    token = secrets.token_urlsafe(12)
                    inserted = await connection.fetchval(
                        """
                        INSERT INTO writers_submission_moderation_tokens(
                            token,
                            submission_id,
                            revision_id,
                            created_at
                        )
                        VALUES($1, $2, $3, $4)
                        ON CONFLICT DO NOTHING
                        RETURNING token
                        """,
                        token,
                        submission_id,
                        revision_id,
                        int(now),
                    )
                    if inserted is not None:
                        return str(inserted)

                    existing = await connection.fetchval(
                        """
                        SELECT token
                        FROM writers_submission_moderation_tokens
                        WHERE submission_id = $1 AND revision_id = $2
                        """,
                        submission_id,
                        revision_id,
                    )
                    if existing is not None:
                        return str(existing)

        raise RuntimeError("Could not allocate moderation token")

    async def resolve_moderation_token(
        self,
        token: str,
    ) -> ModerationTarget:
        token = str(token).strip()
        if not token:
            raise NotFoundError("Moderation token was not found")
        row = await self._require_pool().fetchrow(
            """
            SELECT submission_id, revision_id
            FROM writers_submission_moderation_tokens
            WHERE token = $1
            """,
            token,
        )
        if row is None:
            raise NotFoundError("Moderation token was not found")
        return ModerationTarget(
            submission_id=_uuid(row["submission_id"]),
            revision_id=_uuid(row["revision_id"]),
        )

    async def claim_submission(
        self,
        *,
        submission_id: UUID,
        revision_id: UUID,
        reviewer_user_id: int,
        now: int,
    ) -> ModerationResult:
        pool = self._require_pool()
        reviewer_user_id = int(reviewer_user_id)
        now = int(now)

        async with pool.acquire() as connection:
            async with connection.transaction():
                submission = await connection.fetchrow(
                    """
                    SELECT *
                    FROM writers_submissions
                    WHERE id = $1
                    FOR UPDATE
                    """,
                    submission_id,
                )
                if (
                    submission is None
                    or _uuid(submission["current_submitted_revision_id"]) != revision_id
                ):
                    raise NotFoundError("Submission revision was not found")

                status = SubmissionStatus(str(submission["status"]))
                claimed_by = (
                    int(submission["claimed_by_user_id"])
                    if submission["claimed_by_user_id"] is not None
                    else None
                )
                if status is SubmissionStatus.IN_REVIEW and claimed_by == reviewer_user_id:
                    bundle = await self._bundle_on_connection(
                        connection,
                        submission_id=submission_id,
                        author_user_id=int(submission["author_user_id"]),
                    )
                    assert bundle is not None
                    return ModerationResult(
                        submission=bundle,
                        action=ReviewAction.CLAIM,
                        reviewer_user_id=reviewer_user_id,
                        applied=False,
                        comment=None,
                    )
                if status is not SubmissionStatus.SUBMITTED:
                    raise ConflictError("Submission is not available for claim")

                updated = await connection.execute(
                    """
                    UPDATE writers_submissions
                    SET status = 'IN_REVIEW',
                        claimed_by_user_id = $3,
                        claimed_at = $4,
                        updated_at = $4,
                        version = version + 1
                    WHERE id = $1
                      AND current_submitted_revision_id = $2
                      AND status = 'SUBMITTED'
                    """,
                    submission_id,
                    revision_id,
                    reviewer_user_id,
                    now,
                )
                if updated != "UPDATE 1":
                    raise ConflictError("Submission was claimed concurrently")

                await connection.execute(
                    """
                    INSERT INTO writers_submission_reviews(
                        id,
                        submission_id,
                        revision_id,
                        reviewer_user_id,
                        action,
                        comment,
                        created_at
                    )
                    VALUES($1, $2, $3, $4, 'CLAIM', NULL, $5)
                    """,
                    uuid4(),
                    submission_id,
                    revision_id,
                    reviewer_user_id,
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
                    VALUES($1, $2, $3, 'REVIEW_CLAIMED', '{}', $4)
                    """,
                    submission_id,
                    revision_id,
                    reviewer_user_id,
                    now,
                )
                bundle = await self._bundle_on_connection(
                    connection,
                    submission_id=submission_id,
                    author_user_id=int(submission["author_user_id"]),
                )
        assert bundle is not None
        return ModerationResult(
            submission=bundle,
            action=ReviewAction.CLAIM,
            reviewer_user_id=reviewer_user_id,
            applied=True,
            comment=None,
        )

    async def decide_submission(
        self,
        *,
        submission_id: UUID,
        revision_id: UUID,
        reviewer_user_id: int,
        action: ReviewAction,
        comment: str | None,
        now: int,
    ) -> ModerationResult:
        action = ReviewAction(action)
        if action is ReviewAction.CLAIM:
            raise ValueError("CLAIM is not a decision action")
        target_status = {
            ReviewAction.APPROVE: SubmissionStatus.APPROVED,
            ReviewAction.REQUEST_CHANGES: SubmissionStatus.CHANGES_REQUESTED,
            ReviewAction.REJECT: SubmissionStatus.REJECTED,
        }[action]
        reviewer_user_id = int(reviewer_user_id)
        now = int(now)
        pool = self._require_pool()

        async with pool.acquire() as connection:
            async with connection.transaction():
                submission = await connection.fetchrow(
                    """
                    SELECT *
                    FROM writers_submissions
                    WHERE id = $1
                    FOR UPDATE
                    """,
                    submission_id,
                )
                if (
                    submission is None
                    or _uuid(submission["current_submitted_revision_id"]) != revision_id
                ):
                    raise NotFoundError("Submission revision was not found")

                previous = await connection.fetchrow(
                    """
                    SELECT action, comment
                    FROM writers_submission_reviews
                    WHERE submission_id = $1
                      AND revision_id = $2
                      AND reviewer_user_id = $3
                      AND action = $4
                    ORDER BY created_at DESC, id DESC
                    LIMIT 1
                    """,
                    submission_id,
                    revision_id,
                    reviewer_user_id,
                    action.value,
                )
                if (
                    previous is not None
                    and str(submission["status"]) == target_status.value
                ):
                    bundle = await self._bundle_on_connection(
                        connection,
                        submission_id=submission_id,
                        author_user_id=int(submission["author_user_id"]),
                    )
                    assert bundle is not None
                    return ModerationResult(
                        submission=bundle,
                        action=action,
                        reviewer_user_id=reviewer_user_id,
                        applied=False,
                        comment=(
                            str(previous["comment"])
                            if previous["comment"] is not None
                            else None
                        ),
                    )

                if str(submission["status"]) != SubmissionStatus.IN_REVIEW.value:
                    raise ConflictError("Submission is not in review")
                if int(submission["claimed_by_user_id"] or 0) != reviewer_user_id:
                    raise ConflictError("Only the claimant can decide this submission")

                updated = await connection.execute(
                    """
                    UPDATE writers_submissions
                    SET status = $3,
                        updated_at = $4,
                        version = version + 1
                    WHERE id = $1
                      AND current_submitted_revision_id = $2
                      AND status = 'IN_REVIEW'
                      AND claimed_by_user_id = $5
                    """,
                    submission_id,
                    revision_id,
                    target_status.value,
                    now,
                    reviewer_user_id,
                )
                if updated != "UPDATE 1":
                    raise ConflictError("Submission changed during moderation")

                await connection.execute(
                    """
                    INSERT INTO writers_submission_reviews(
                        id,
                        submission_id,
                        revision_id,
                        reviewer_user_id,
                        action,
                        comment,
                        created_at
                    )
                    VALUES($1, $2, $3, $4, $5, $6, $7)
                    """,
                    uuid4(),
                    submission_id,
                    revision_id,
                    reviewer_user_id,
                    action.value,
                    comment,
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
                    VALUES($1, $2, $3, 'REVIEW_DECIDED', $4, $5)
                    """,
                    submission_id,
                    revision_id,
                    reviewer_user_id,
                    json.dumps({"action": action.value}, separators=(",", ":")),
                    now,
                )
                await connection.execute(
                    """
                    INSERT INTO writers_submission_outbox(
                        id,
                        submission_id,
                        revision_id,
                        event_type,
                        state,
                        attempt_count,
                        next_attempt_at,
                        payload_json,
                        dedupe_key,
                        created_at,
                        updated_at
                    )
                    VALUES(
                        $1, $2, $3, 'AUTHOR_NOTIFICATION', 'PENDING',
                        0, $4, $5, $6, $4, $4
                    )
                    ON CONFLICT (dedupe_key) DO NOTHING
                    """,
                    uuid4(),
                    submission_id,
                    revision_id,
                    now,
                    json.dumps(
                        {"action": action.value},
                        separators=(",", ":"),
                    ),
                    f"author:{submission_id}:{revision_id}:{action.value}",
                )
                bundle = await self._bundle_on_connection(
                    connection,
                    submission_id=submission_id,
                    author_user_id=int(submission["author_user_id"]),
                )
        assert bundle is not None
        return ModerationResult(
            submission=bundle,
            action=action,
            reviewer_user_id=reviewer_user_id,
            applied=True,
            comment=comment,
        )

    async def get_moderation_delivery_context(
        self,
        *,
        submission_id: UUID,
        revision_id: UUID,
    ) -> ModerationDeliveryContext:
        pool = self._require_pool()
        row = await pool.fetchrow(
            """
            SELECT
                s.author_user_id,
                r.title,
                r.work_type,
                r.genre,
                r.description,
                r.body_text,
                r.external_url
            FROM writers_submissions AS s
            JOIN writers_submission_revisions AS r
              ON r.submission_id = s.id
            WHERE s.id = $1 AND r.id = $2
            """,
            submission_id,
            revision_id,
        )
        if row is None:
            raise NotFoundError("Submission revision was not found")
        file_rows = await pool.fetch(
            """
            SELECT *
            FROM writers_submission_files
            WHERE submission_id = $1 AND revision_id = $2
            ORDER BY created_at, id
            """,
            submission_id,
            revision_id,
        )
        return ModerationDeliveryContext(
            author_user_id=int(row["author_user_id"]),
            title=str(row["title"]),
            work_type=str(row["work_type"]),
            genre=str(row["genre"]),
            description=str(row["description"]),
            body_text=str(row["body_text"]),
            external_url=(
                str(row["external_url"])
                if row["external_url"] is not None
                else None
            ),
            files=tuple(_file_from_row(item) for item in file_rows),
        )

    async def get_author_notification_context(
        self,
        *,
        submission_id: UUID,
        revision_id: UUID,
        notification_kind: str | None = None,
    ) -> AuthorNotificationContext:
        pool = self._require_pool()
        kind = str(notification_kind).strip() if notification_kind else None

        if kind in {"SUBMISSION_ACCEPTED", "WITHDRAWN"}:
            row = await pool.fetchrow(
                """
                SELECT
                    s.author_user_id,
                    r.title
                FROM writers_submissions AS s
                JOIN writers_submission_revisions AS r
                  ON r.submission_id = s.id
                WHERE s.id = $1 AND r.id = $2
                """,
                submission_id,
                revision_id,
            )
            if row is None:
                raise NotFoundError("Author notification context was not found")
            return AuthorNotificationContext(
                author_user_id=int(row["author_user_id"]),
                title=str(row["title"]),
                action=kind,
                comment=None,
            )

        action_map = {
            "APPROVED": "APPROVE",
            "APPROVE": "APPROVE",
            "CHANGES_REQUESTED": "REQUEST_CHANGES",
            "REQUEST_CHANGES": "REQUEST_CHANGES",
            "REJECTED": "REJECT",
            "REJECT": "REJECT",
        }
        review_action = action_map.get(kind) if kind else None
        if review_action is None:
            row = await pool.fetchrow(
                """
                SELECT
                    s.author_user_id,
                    r.title,
                    review.action,
                    review.comment
                FROM writers_submissions AS s
                JOIN writers_submission_revisions AS r
                  ON r.submission_id = s.id
                JOIN LATERAL (
                    SELECT action, comment
                    FROM writers_submission_reviews
                    WHERE submission_id = s.id
                      AND revision_id = r.id
                      AND action IN ('APPROVE', 'REQUEST_CHANGES', 'REJECT')
                    ORDER BY created_at DESC, id DESC
                    LIMIT 1
                ) AS review ON TRUE
                WHERE s.id = $1 AND r.id = $2
                """,
                submission_id,
                revision_id,
            )
            if row is None:
                raise NotFoundError("Author notification context was not found")
            action: ReviewAction | str = ReviewAction(str(row["action"]))
        else:
            row = await pool.fetchrow(
                """
                SELECT
                    s.author_user_id,
                    r.title,
                    review.action,
                    review.comment
                FROM writers_submissions AS s
                JOIN writers_submission_revisions AS r
                  ON r.submission_id = s.id
                JOIN writers_submission_reviews AS review
                  ON review.submission_id = s.id
                 AND review.revision_id = r.id
                 AND review.action = $3
                WHERE s.id = $1 AND r.id = $2
                ORDER BY review.created_at DESC, review.id DESC
                LIMIT 1
                """,
                submission_id,
                revision_id,
                review_action,
            )
            if row is None:
                raise NotFoundError("Author notification context was not found")
            action = kind or ReviewAction(str(row["action"]))

        return AuthorNotificationContext(
            author_user_id=int(row["author_user_id"]),
            title=str(row["title"]),
            action=action,
            comment=(
                str(row["comment"]) if row["comment"] is not None else None
            ),
        )

    async def claim_due_outbox(
        self,
        *,
        worker_id: str,
        now: int,
        lease_seconds: int,
        limit: int,
    ) -> list[OutboxRecord]:
        worker_id = str(worker_id).strip()
        if not worker_id:
            raise ValueError("worker_id is required")
        lease_seconds = int(lease_seconds)
        limit = int(limit)
        if lease_seconds <= 0 or limit <= 0:
            raise ValueError("lease_seconds and limit must be positive")
        now = int(now)
        rows = await self._require_pool().fetch(
            """
            WITH candidates AS (
                SELECT id
                FROM writers_submission_outbox
                WHERE (
                    state IN ('PENDING', 'RETRYABLE_FAILED')
                    AND next_attempt_at <= $1
                ) OR (
                    state = 'IN_FLIGHT'
                    AND lease_until IS NOT NULL
                    AND lease_until <= $1
                )
                ORDER BY next_attempt_at, created_at, id
                FOR UPDATE SKIP LOCKED
                LIMIT $2
            )
            UPDATE writers_submission_outbox AS o
            SET state = 'IN_FLIGHT',
                worker_id = $3,
                lease_until = $1 + $4,
                attempt_count = o.attempt_count + 1,
                last_error_code = NULL,
                updated_at = $1
            FROM candidates
            WHERE o.id = candidates.id
            RETURNING o.*
            """,
            now,
            limit,
            worker_id,
            lease_seconds,
        )
        return [_outbox_from_row(row) for row in rows]

    async def mark_outbox_delivered(
        self,
        *,
        outbox_id: UUID,
        worker_id: str,
        now: int,
        delivery_chat_id: int | None = None,
        delivery_message_ids: tuple[int, ...] = (),
    ) -> OutboxRecord:
        row = await self._require_pool().fetchrow(
            """
            UPDATE writers_submission_outbox
            SET state = 'DELIVERED',
                lease_until = NULL,
                worker_id = NULL,
                last_error_code = NULL,
                delivery_chat_id = $4,
                delivery_message_ids_json = $5,
                updated_at = $3
            WHERE id = $1
              AND state = 'IN_FLIGHT'
              AND worker_id = $2
            RETURNING *
            """,
            outbox_id,
            str(worker_id),
            int(now),
            (
                int(delivery_chat_id)
                if delivery_chat_id is not None
                else None
            ),
            json.dumps(
                [int(value) for value in delivery_message_ids],
                separators=(",", ":"),
            ),
        )
        if row is None:
            raise ConflictError("Outbox item is not leased by this worker")
        return _outbox_from_row(row)

    async def mark_outbox_retryable(
        self,
        *,
        outbox_id: UUID,
        worker_id: str,
        now: int,
        next_attempt_at: int,
        error_code: str,
    ) -> OutboxRecord:
        row = await self._require_pool().fetchrow(
            """
            UPDATE writers_submission_outbox
            SET state = 'RETRYABLE_FAILED',
                next_attempt_at = $3,
                lease_until = NULL,
                worker_id = NULL,
                last_error_code = $4,
                updated_at = $5
            WHERE id = $1
              AND state = 'IN_FLIGHT'
              AND worker_id = $2
            RETURNING *
            """,
            outbox_id,
            str(worker_id),
            int(next_attempt_at),
            str(error_code)[:120],
            int(now),
        )
        if row is None:
            raise ConflictError("Outbox item is not leased by this worker")
        return _outbox_from_row(row)

    async def mark_outbox_permanent_failure(
        self,
        *,
        outbox_id: UUID,
        worker_id: str,
        now: int,
        error_code: str,
    ) -> OutboxRecord:
        row = await self._require_pool().fetchrow(
            """
            UPDATE writers_submission_outbox
            SET state = 'PERMANENT_FAILED',
                lease_until = NULL,
                worker_id = NULL,
                last_error_code = $3,
                updated_at = $4
            WHERE id = $1
              AND state = 'IN_FLIGHT'
              AND worker_id = $2
            RETURNING *
            """,
            outbox_id,
            str(worker_id),
            str(error_code)[:120],
            int(now),
        )
        if row is None:
            raise ConflictError("Outbox item is not leased by this worker")
        return _outbox_from_row(row)

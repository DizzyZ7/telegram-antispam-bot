from __future__ import annotations

from enum import StrEnum


class SubmissionStatus(StrEnum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"


class RevisionState(StrEnum):
    DRAFT = "DRAFT"
    SEALED = "SEALED"


class ReviewAction(StrEnum):
    CLAIM = "CLAIM"
    APPROVE = "APPROVE"
    REQUEST_CHANGES = "REQUEST_CHANGES"
    REJECT = "REJECT"


class OutboxState(StrEnum):
    PENDING = "PENDING"
    IN_FLIGHT = "IN_FLIGHT"
    DELIVERED = "DELIVERED"
    RETRYABLE_FAILED = "RETRYABLE_FAILED"
    PERMANENT_FAILED = "PERMANENT_FAILED"


class OutboxEventType(StrEnum):
    MODERATION_CARD = "MODERATION_CARD"
    AUTHOR_NOTIFICATION = "AUTHOR_NOTIFICATION"


class WritersSubmissionError(Exception):
    """Base domain error for Writers Submission."""


class AuthorizationError(WritersSubmissionError):
    """The actor is not permitted to perform the operation."""


class ConflictError(WritersSubmissionError):
    """The requested write conflicts with newer durable state."""


class ValidationError(WritersSubmissionError):
    """User-controlled input failed a domain validation rule."""


class NotFoundError(WritersSubmissionError):
    """The requested owned object does not exist or is not visible to the actor."""


from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class SubmissionRevision:
    id: UUID
    submission_id: UUID
    revision_number: int
    state: RevisionState
    title: str
    work_type: str
    genre: str
    description: str
    body_text: str
    external_url: str | None
    created_at: int
    updated_at: int
    sealed_at: int | None


@dataclass(frozen=True, slots=True)
class SubmissionBundle:
    id: UUID
    writers_chat_id: int
    author_user_id: int
    status: SubmissionStatus
    current_draft_revision_id: UUID | None
    current_submitted_revision_id: UUID | None
    claimed_by_user_id: int | None
    claimed_at: int | None
    created_at: int
    updated_at: int
    version: int
    revision: SubmissionRevision


@dataclass(frozen=True, slots=True)
class SubmissionSummary:
    id: UUID
    status: SubmissionStatus
    title: str
    revision_number: int
    updated_at: int
    version: int


@dataclass(frozen=True, slots=True)
class DraftFileContext:
    revision_id: UUID
    file_count: int


@dataclass(frozen=True, slots=True)
class SubmissionFile:
    id: UUID
    submission_id: UUID
    revision_id: UUID
    safe_filename: str
    declared_mime: str
    detected_file_class: str
    byte_size: int
    sha256: str
    telegram_file_id: str
    telegram_file_unique_id: str | None
    storage_chat_id: int | None
    storage_message_id: int | None
    created_at: int


@dataclass(frozen=True, slots=True)
class OutboxRecord:
    id: UUID
    submission_id: UUID
    revision_id: UUID | None
    event_type: OutboxEventType
    state: OutboxState
    attempt_count: int
    next_attempt_at: int
    lease_until: int | None
    worker_id: str | None
    last_error_code: str | None
    payload: dict[str, object]
    dedupe_key: str | None
    created_at: int
    updated_at: int


@dataclass(frozen=True, slots=True)
class ModerationResult:
    submission: SubmissionBundle
    action: ReviewAction
    reviewer_user_id: int
    applied: bool
    comment: str | None


@dataclass(frozen=True, slots=True)
class ModerationDeliveryContext:
    author_user_id: int
    title: str
    work_type: str
    genre: str
    description: str
    body_text: str
    external_url: str | None
    files: tuple[SubmissionFile, ...]


@dataclass(frozen=True, slots=True)
class AuthorNotificationContext:
    author_user_id: int
    title: str
    action: ReviewAction
    comment: str | None


@dataclass(frozen=True, slots=True)
class ModerationTarget:
    submission_id: UUID
    revision_id: UUID

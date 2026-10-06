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

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from .models import (
    AuthorizationError,
    ConflictError,
    ModerationResult,
    NotFoundError,
    ReviewAction,
    SubmissionBundle,
    SubmissionSummary,
)
from .uploads import NormalizedSubmissionFields

WritersEligibilityChecker = Callable[[int], Awaitable[bool]]


class WritersSubmissionService:
    def __init__(
        self,
        storage: Any,
        config: Any,
        eligibility_checker: WritersEligibilityChecker,
    ) -> None:
        self.storage = storage
        self.config = config
        self.eligibility_checker = eligibility_checker

    async def _require_eligible(self, author_user_id: int) -> None:
        if not await self.eligibility_checker(int(author_user_id)):
            raise PermissionError("Current writers community membership is required")

    async def _require_owned(
        self,
        author_user_id: int,
        submission_id: UUID,
    ) -> SubmissionBundle:
        item = await self.storage.get_for_author(
            submission_id,
            int(author_user_id),
        )
        if item is None:
            raise NotFoundError("Submission was not found")
        return item

    async def create(
        self,
        *,
        author_user_id: int,
        fields: NormalizedSubmissionFields,
        idempotency_key: str,
        now: int,
    ) -> SubmissionBundle:
        await self._require_eligible(author_user_id)
        writers_chat_id = getattr(self.config, "writers_chat_id", None)
        if writers_chat_id is None:
            raise RuntimeError("Writers chat id is not configured")
        return await self.storage.create_submission(
            author_user_id=int(author_user_id),
            writers_chat_id=int(writers_chat_id),
            fields=fields,
            now=int(now),
            idempotency_key=str(idempotency_key),
        )

    async def list_mine(
        self,
        author_user_id: int,
    ) -> list[SubmissionSummary]:
        return await self.storage.list_for_author(int(author_user_id))

    async def get_mine(
        self,
        author_user_id: int,
        submission_id: UUID,
    ) -> SubmissionBundle:
        return await self._require_owned(author_user_id, submission_id)

    async def autosave(
        self,
        *,
        author_user_id: int,
        submission_id: UUID,
        expected_version: int,
        fields: NormalizedSubmissionFields,
        now: int,
    ) -> SubmissionBundle:
        await self._require_owned(author_user_id, submission_id)
        return await self.storage.update_draft(
            submission_id=submission_id,
            author_user_id=int(author_user_id),
            expected_version=int(expected_version),
            fields=fields,
            now=int(now),
        )

    async def withdraw(
        self,
        *,
        author_user_id: int,
        submission_id: UUID,
        idempotency_key: str,
        now: int,
    ) -> SubmissionBundle:
        await self._require_owned(author_user_id, submission_id)
        return await self.storage.withdraw_submission(
            submission_id=submission_id,
            author_user_id=int(author_user_id),
            idempotency_key=str(idempotency_key),
            now=int(now),
        )

    async def create_revision(
        self,
        *,
        author_user_id: int,
        submission_id: UUID,
        idempotency_key: str,
        now: int,
    ) -> SubmissionBundle:
        await self._require_owned(author_user_id, submission_id)
        return await self.storage.create_revision(
            submission_id=submission_id,
            author_user_id=int(author_user_id),
            idempotency_key=str(idempotency_key),
            now=int(now),
        )


    async def submit(
        self,
        *,
        author_user_id: int,
        submission_id: UUID,
        expected_version: int,
        idempotency_key: str,
        now: int,
    ) -> SubmissionBundle:
        await self._require_owned(author_user_id, submission_id)
        await self._require_eligible(author_user_id)
        return await self.storage.seal_and_submit(
            submission_id=submission_id,
            author_user_id=int(author_user_id),
            expected_version=int(expected_version),
            idempotency_key=str(idempotency_key),
            now=int(now),
        )


    def _require_reviewer(self, reviewer_user_id: int) -> None:
        moderator_ids = frozenset(
            int(value) for value in getattr(self.config, "moderator_ids", frozenset())
        )
        if int(reviewer_user_id) not in moderator_ids:
            raise AuthorizationError("Reviewer is not allowlisted")

    async def claim(
        self,
        *,
        reviewer_user_id: int,
        submission_id: UUID,
        revision_id: UUID,
        now: int,
    ) -> ModerationResult:
        self._require_reviewer(reviewer_user_id)
        return await self.storage.claim_submission(
            submission_id=submission_id,
            revision_id=revision_id,
            reviewer_user_id=int(reviewer_user_id),
            now=int(now),
        )

    async def decide(
        self,
        *,
        reviewer_user_id: int,
        submission_id: UUID,
        revision_id: UUID,
        action: ReviewAction,
        comment: str | None,
        now: int,
    ) -> ModerationResult:
        self._require_reviewer(reviewer_user_id)
        try:
            action = ReviewAction(action)
        except (TypeError, ValueError) as exc:
            raise ValueError("Unsupported moderation action") from exc
        if action is ReviewAction.CLAIM:
            raise ValueError("CLAIM is not a decision action")

        normalized_comment = comment.strip() if isinstance(comment, str) else None
        if normalized_comment == "":
            normalized_comment = None
        if action is ReviewAction.REQUEST_CHANGES and not normalized_comment:
            raise ValueError("request changes requires a comment")
        if normalized_comment is not None and len(normalized_comment) > 2000:
            raise ValueError("moderation comment is too long")

        return await self.storage.decide_submission(
            submission_id=submission_id,
            revision_id=revision_id,
            reviewer_user_id=int(reviewer_user_id),
            action=action,
            comment=normalized_comment,
            now=int(now),
        )

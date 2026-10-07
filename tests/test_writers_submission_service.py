from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from writers_submission.models import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ReviewAction,
    SubmissionStatus,
)
from writers_submission.service import WritersSubmissionService
from writers_submission.uploads import NormalizedSubmissionFields


def fields(title: str = "Работа") -> NormalizedSubmissionFields:
    return NormalizedSubmissionFields(
        title=title,
        work_type="Рассказ",
        genre="Фантастика",
        description="Описание",
        body_text="Текст",
        external_url=None,
    )


def bundle(
    *,
    submission_id=None,
    author_user_id=77,
    status=SubmissionStatus.DRAFT,
    version=1,
    revision_number=1,
    title="Работа",
):
    submission_id = submission_id or uuid4()
    revision_id = uuid4()
    revision = SimpleNamespace(
        id=revision_id,
        submission_id=submission_id,
        revision_number=revision_number,
        title=title,
    )
    return SimpleNamespace(
        id=submission_id,
        author_user_id=author_user_id,
        status=status,
        version=version,
        revision=revision,
        current_draft_revision_id=(
            revision_id if status in {SubmissionStatus.DRAFT, SubmissionStatus.CHANGES_REQUESTED} else None
        ),
        current_submitted_revision_id=None,
        claimed_by_user_id=None,
        claimed_at=None,
    )


class FakeStorage:
    def __init__(self):
        self.items = {}
        self.idempotent_create = {}
        self.create_calls = 0
        self.submit_calls = 0
        self.idempotent_submit = {}
        self.decision_calls = 0
        self.decisions = {}

    async def create_submission(
        self,
        *,
        author_user_id,
        writers_chat_id,
        fields,
        now,
        idempotency_key,
    ):
        key = (author_user_id, idempotency_key)
        if key in self.idempotent_create:
            return self.idempotent_create[key]
        self.create_calls += 1
        item = bundle(author_user_id=author_user_id, title=fields.title)
        self.items[item.id] = item
        self.idempotent_create[key] = item
        return item

    async def list_for_author(self, author_user_id, limit=100):
        return [
            SimpleNamespace(id=item.id, title=item.revision.title)
            for item in self.items.values()
            if item.author_user_id == author_user_id
        ]

    async def get_for_author(self, submission_id, author_user_id):
        item = self.items.get(submission_id)
        if item is None or item.author_user_id != author_user_id:
            return None
        return item

    async def update_draft(
        self,
        *,
        submission_id,
        author_user_id,
        expected_version,
        fields,
        now,
    ):
        item = await self.get_for_author(submission_id, author_user_id)
        if item is None:
            raise NotFoundError("not found")
        if item.version != expected_version:
            raise ConflictError("stale")
        item.version += 1
        item.revision.title = fields.title
        return item

    async def seal_and_submit(
        self,
        *,
        submission_id,
        author_user_id,
        expected_version,
        idempotency_key,
        now,
    ):
        key = (author_user_id, idempotency_key)
        if key in self.idempotent_submit:
            return self.idempotent_submit[key]
        item = await self.get_for_author(submission_id, author_user_id)
        if item is None:
            raise NotFoundError("not found")
        if item.version != expected_version:
            raise ConflictError("stale")
        self.submit_calls += 1
        item.status = SubmissionStatus.SUBMITTED
        item.version += 1
        item.current_submitted_revision_id = item.revision.id
        item.current_draft_revision_id = None
        self.idempotent_submit[key] = item
        return item


    async def claim_submission(
        self,
        *,
        submission_id,
        revision_id,
        reviewer_user_id,
        now,
    ):
        item = self.items.get(submission_id)
        if item is None or item.current_submitted_revision_id != revision_id:
            raise NotFoundError("not found")
        if item.status is SubmissionStatus.SUBMITTED:
            item.status = SubmissionStatus.IN_REVIEW
            item.claimed_by_user_id = reviewer_user_id
            item.claimed_at = now
            return SimpleNamespace(
                submission=item,
                action=ReviewAction.CLAIM,
                reviewer_user_id=reviewer_user_id,
                applied=True,
                comment=None,
            )
        if (
            item.status is SubmissionStatus.IN_REVIEW
            and item.claimed_by_user_id == reviewer_user_id
        ):
            return SimpleNamespace(
                submission=item,
                action=ReviewAction.CLAIM,
                reviewer_user_id=reviewer_user_id,
                applied=False,
                comment=None,
            )
        raise ConflictError("already claimed")

    async def decide_submission(
        self,
        *,
        submission_id,
        revision_id,
        reviewer_user_id,
        action,
        comment,
        now,
    ):
        key = (submission_id, revision_id, reviewer_user_id, action)
        if key in self.decisions:
            return self.decisions[key]
        item = self.items.get(submission_id)
        if item is None or item.current_submitted_revision_id != revision_id:
            raise NotFoundError("not found")
        if (
            item.status is not SubmissionStatus.IN_REVIEW
            or item.claimed_by_user_id != reviewer_user_id
        ):
            raise ConflictError("not claimant")
        target = {
            ReviewAction.APPROVE: SubmissionStatus.APPROVED,
            ReviewAction.REQUEST_CHANGES: SubmissionStatus.CHANGES_REQUESTED,
            ReviewAction.REJECT: SubmissionStatus.REJECTED,
        }[action]
        item.status = target
        self.decision_calls += 1
        result = SimpleNamespace(
            submission=item,
            action=action,
            reviewer_user_id=reviewer_user_id,
            applied=True,
            comment=comment,
        )
        self.decisions[key] = result
        return result


    async def withdraw_submission(
        self,
        *,
        submission_id,
        author_user_id,
        idempotency_key,
        now,
    ):
        item = await self.get_for_author(submission_id, author_user_id)
        if item is None:
            raise NotFoundError("not found")
        if item.status in {
            SubmissionStatus.APPROVED,
            SubmissionStatus.REJECTED,
            SubmissionStatus.WITHDRAWN,
        }:
            raise ConflictError("terminal")
        item.status = SubmissionStatus.WITHDRAWN
        return item

    async def create_revision(
        self,
        *,
        submission_id,
        author_user_id,
        idempotency_key,
        now,
    ):
        item = await self.get_for_author(submission_id, author_user_id)
        if item is None:
            raise NotFoundError("not found")
        if item.status is not SubmissionStatus.CHANGES_REQUESTED:
            raise ConflictError("changes not requested")
        previous_title = item.revision.title
        item.status = SubmissionStatus.DRAFT
        item.version += 1
        item.revision = SimpleNamespace(
            id=uuid4(),
            submission_id=item.id,
            revision_number=item.revision.revision_number + 1,
            title=previous_title,
        )
        item.current_draft_revision_id = item.revision.id
        return item


class WritersSubmissionServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.storage = FakeStorage()
        self.eligibility = AsyncMock(return_value=True)
        self.config = SimpleNamespace(
            writers_chat_id=-1002619489118,
            moderator_ids=frozenset({9001, 9002}),
        )
        self.service = WritersSubmissionService(
            self.storage,
            self.config,
            self.eligibility,
        )

    async def test_create_requires_current_writers_eligibility(self):
        self.eligibility.return_value = False

        with self.assertRaisesRegex(PermissionError, "writers community"):
            await self.service.create(
                author_user_id=77,
                fields=fields(),
                idempotency_key="create-1",
                now=100,
            )

        self.assertEqual(self.storage.create_calls, 0)
        self.eligibility.assert_awaited_once_with(77)

    async def test_create_is_idempotent_through_storage_key(self):
        first = await self.service.create(
            author_user_id=77,
            fields=fields(),
            idempotency_key="same-create",
            now=100,
        )
        second = await self.service.create(
            author_user_id=77,
            fields=fields("Ignored duplicate"),
            idempotency_key="same-create",
            now=101,
        )

        self.assertEqual(first.id, second.id)
        self.assertEqual(self.storage.create_calls, 1)

    async def test_owned_history_and_autosave_do_not_require_current_membership(self):
        created = await self.service.create(
            author_user_id=77,
            fields=fields(),
            idempotency_key="create-2",
            now=100,
        )
        self.eligibility.reset_mock()
        self.eligibility.return_value = False

        listing = await self.service.list_mine(77)
        loaded = await self.service.get_mine(77, created.id)
        updated = await self.service.autosave(
            author_user_id=77,
            submission_id=created.id,
            expected_version=1,
            fields=fields("После выхода"),
            now=101,
        )

        self.assertEqual(len(listing), 1)
        self.assertEqual(loaded.id, created.id)
        self.assertEqual(updated.revision.title, "После выхода")
        self.eligibility.assert_not_awaited()

    async def test_cross_user_detail_autosave_withdraw_and_revision_are_hidden(self):
        created = await self.service.create(
            author_user_id=77,
            fields=fields(),
            idempotency_key="create-3",
            now=100,
        )

        with self.assertRaises(NotFoundError):
            await self.service.get_mine(88, created.id)
        with self.assertRaises(NotFoundError):
            await self.service.autosave(
                author_user_id=88,
                submission_id=created.id,
                expected_version=1,
                fields=fields("Чужая запись"),
                now=101,
            )
        with self.assertRaises(NotFoundError):
            await self.service.withdraw(
                author_user_id=88,
                submission_id=created.id,
                idempotency_key="withdraw-x",
                now=102,
            )
        with self.assertRaises(NotFoundError):
            await self.service.create_revision(
                author_user_id=88,
                submission_id=created.id,
                idempotency_key="revision-x",
                now=103,
            )

    async def test_two_tab_stale_autosave_conflicts(self):
        created = await self.service.create(
            author_user_id=77,
            fields=fields(),
            idempotency_key="create-4",
            now=100,
        )
        await self.service.autosave(
            author_user_id=77,
            submission_id=created.id,
            expected_version=1,
            fields=fields("Победитель"),
            now=101,
        )

        with self.assertRaises(ConflictError):
            await self.service.autosave(
                author_user_id=77,
                submission_id=created.id,
                expected_version=1,
                fields=fields("Устаревшая вкладка"),
                now=102,
            )

    async def test_terminal_submission_cannot_be_withdrawn(self):
        created = await self.service.create(
            author_user_id=77,
            fields=fields(),
            idempotency_key="create-5",
            now=100,
        )
        created.status = SubmissionStatus.APPROVED

        with self.assertRaises(ConflictError):
            await self.service.withdraw(
                author_user_id=77,
                submission_id=created.id,
                idempotency_key="withdraw-terminal",
                now=101,
            )

    async def test_changes_requested_creates_prefilled_next_revision(self):
        created = await self.service.create(
            author_user_id=77,
            fields=fields("Редакция один"),
            idempotency_key="create-6",
            now=100,
        )
        original_revision_id = created.revision.id
        original_title = created.revision.title
        created.status = SubmissionStatus.CHANGES_REQUESTED

        revised = await self.service.create_revision(
            author_user_id=77,
            submission_id=created.id,
            idempotency_key="revision-2",
            now=110,
        )

        self.assertEqual(revised.status, SubmissionStatus.DRAFT)
        self.assertEqual(revised.revision.revision_number, 2)
        self.assertEqual(revised.revision.title, original_title)
        self.assertNotEqual(revised.revision.id, original_revision_id)


    async def test_submit_requires_fresh_writers_eligibility(self):
        created = await self.service.create(
            author_user_id=77,
            fields=fields(),
            idempotency_key="create-submit-eligibility",
            now=100,
        )
        self.eligibility.reset_mock()
        self.eligibility.return_value = False

        with self.assertRaisesRegex(PermissionError, "writers community"):
            await self.service.submit(
                author_user_id=77,
                submission_id=created.id,
                expected_version=1,
                idempotency_key="submit-eligibility",
                now=101,
            )

        self.eligibility.assert_awaited_once_with(77)
        self.assertEqual(self.storage.submit_calls, 0)

    async def test_submit_delegates_exact_actor_version_and_idempotency_key(self):
        created = await self.service.create(
            author_user_id=77,
            fields=fields(),
            idempotency_key="create-submit",
            now=100,
        )
        self.eligibility.reset_mock()

        submitted = await self.service.submit(
            author_user_id=77,
            submission_id=created.id,
            expected_version=1,
            idempotency_key="submit-1",
            now=101,
        )
        duplicate = await self.service.submit(
            author_user_id=77,
            submission_id=created.id,
            expected_version=1,
            idempotency_key="submit-1",
            now=102,
        )

        self.assertEqual(submitted.status, SubmissionStatus.SUBMITTED)
        self.assertEqual(duplicate.id, submitted.id)
        self.assertIsNone(submitted.current_draft_revision_id)
        self.assertEqual(
            submitted.current_submitted_revision_id,
            submitted.revision.id,
        )
        self.assertEqual(self.storage.submit_calls, 1)
        self.assertEqual(self.eligibility.await_count, 2)


    async def _submitted_for_moderation(self, key: str):
        created = await self.service.create(
            author_user_id=77,
            fields=fields(),
            idempotency_key=f"create-{key}",
            now=100,
        )
        return await self.service.submit(
            author_user_id=77,
            submission_id=created.id,
            expected_version=1,
            idempotency_key=f"submit-{key}",
            now=101,
        )

    async def test_non_allowlisted_reviewer_cannot_claim(self):
        submitted = await self._submitted_for_moderation("unauthorized-claim")

        with self.assertRaises(AuthorizationError):
            await self.service.claim(
                reviewer_user_id=9999,
                submission_id=submitted.id,
                revision_id=submitted.revision.id,
                now=110,
            )

        self.assertEqual(submitted.status, SubmissionStatus.SUBMITTED)

    async def test_claim_transitions_submitted_to_in_review_idempotently(self):
        submitted = await self._submitted_for_moderation("claim")

        first = await self.service.claim(
            reviewer_user_id=9001,
            submission_id=submitted.id,
            revision_id=submitted.revision.id,
            now=110,
        )
        second = await self.service.claim(
            reviewer_user_id=9001,
            submission_id=submitted.id,
            revision_id=submitted.revision.id,
            now=111,
        )

        self.assertEqual(first.submission.status, SubmissionStatus.IN_REVIEW)
        self.assertEqual(first.submission.claimed_by_user_id, 9001)
        self.assertTrue(first.applied)
        self.assertFalse(second.applied)

    async def test_only_claimant_can_decide(self):
        submitted = await self._submitted_for_moderation("claimant")
        await self.service.claim(
            reviewer_user_id=9001,
            submission_id=submitted.id,
            revision_id=submitted.revision.id,
            now=110,
        )

        with self.assertRaises(ConflictError):
            await self.service.decide(
                reviewer_user_id=9002,
                submission_id=submitted.id,
                revision_id=submitted.revision.id,
                action=ReviewAction.APPROVE,
                comment=None,
                now=111,
            )

    async def test_decision_actions_transition_and_duplicate_is_idempotent(self):
        cases = (
            (ReviewAction.APPROVE, None, SubmissionStatus.APPROVED),
            (
                ReviewAction.REQUEST_CHANGES,
                "Нужно усилить финал",
                SubmissionStatus.CHANGES_REQUESTED,
            ),
            (ReviewAction.REJECT, "Не подходит формату", SubmissionStatus.REJECTED),
        )
        for index, (action, comment, expected) in enumerate(cases):
            with self.subTest(action=action):
                submitted = await self._submitted_for_moderation(f"decision-{index}")
                await self.service.claim(
                    reviewer_user_id=9001,
                    submission_id=submitted.id,
                    revision_id=submitted.revision.id,
                    now=110,
                )
                first = await self.service.decide(
                    reviewer_user_id=9001,
                    submission_id=submitted.id,
                    revision_id=submitted.revision.id,
                    action=action,
                    comment=comment,
                    now=111,
                )
                before = self.storage.decision_calls
                duplicate = await self.service.decide(
                    reviewer_user_id=9001,
                    submission_id=submitted.id,
                    revision_id=submitted.revision.id,
                    action=action,
                    comment=comment,
                    now=112,
                )

                self.assertEqual(first.submission.status, expected)
                self.assertEqual(duplicate.submission.status, expected)
                self.assertEqual(self.storage.decision_calls, before)

    async def test_request_changes_requires_comment(self):
        submitted = await self._submitted_for_moderation("comment")
        await self.service.claim(
            reviewer_user_id=9001,
            submission_id=submitted.id,
            revision_id=submitted.revision.id,
            now=110,
        )

        with self.assertRaisesRegex(ValueError, "comment"):
            await self.service.decide(
                reviewer_user_id=9001,
                submission_id=submitted.id,
                revision_id=submitted.revision.id,
                action=ReviewAction.REQUEST_CHANGES,
                comment="   ",
                now=111,
            )


if __name__ == "__main__":
    unittest.main()

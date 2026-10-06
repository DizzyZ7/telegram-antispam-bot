from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from writers_submission.models import (
    ConflictError,
    NotFoundError,
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
    )


class FakeStorage:
    def __init__(self):
        self.items = {}
        self.idempotent_create = {}
        self.create_calls = 0
        self.submit_calls = 0
        self.idempotent_submit = {}

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
        self.config = SimpleNamespace(writers_chat_id=-1002619489118)
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


if __name__ == "__main__":
    unittest.main()

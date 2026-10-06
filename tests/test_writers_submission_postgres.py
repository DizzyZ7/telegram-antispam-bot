from __future__ import annotations

import os
import unittest

from writers_submission.models import ConflictError, SubmissionStatus
from writers_submission.storage import PostgresWritersSubmissionStorage
from writers_submission.uploads import NormalizedSubmissionFields

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()


def fields(title: str = "Первая работа") -> NormalizedSubmissionFields:
    return NormalizedSubmissionFields(
        title=title,
        work_type="Рассказ",
        genre="Фантастика",
        description="Описание",
        body_text="Текст работы",
        external_url=None,
    )


@unittest.skipUnless(TEST_DATABASE_URL, "TEST_DATABASE_URL is required")
class WritersSubmissionPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.storage = PostgresWritersSubmissionStorage(TEST_DATABASE_URL)
        await self.storage.initialize()
        assert self.storage.pool is not None
        await self.storage.pool.execute(
            """
            TRUNCATE
                writers_submission_outbox,
                writers_submission_reviews,
                writers_submission_files,
                writers_submission_events,
                writers_submission_idempotency,
                writers_submission_revisions,
                writers_submissions
            RESTART IDENTITY CASCADE
            """
        )

    async def asyncTearDown(self):
        await self.storage.close()

    async def test_initialize_creates_schema_v1_tables_and_indexes(self):
        assert self.storage.pool is not None
        for table in (
            "writers_submission_schema_meta",
            "writers_submissions",
            "writers_submission_revisions",
            "writers_submission_files",
            "writers_submission_reviews",
            "writers_submission_events",
            "writers_submission_outbox",
            "writers_submission_idempotency",
        ):
            with self.subTest(table=table):
                actual = await self.storage.pool.fetchval(
                    "SELECT to_regclass($1)::text",
                    f"public.{table}",
                )
                self.assertEqual(actual, table)

        version = await self.storage.pool.fetchval(
            """
            SELECT value
            FROM writers_submission_schema_meta
            WHERE key = 'schema_version'
            """
        )
        self.assertEqual(version, "1")

    async def test_create_list_get_and_update_owned_draft(self):
        created = await self.storage.create_submission(
            author_user_id=77,
            writers_chat_id=-1002619489118,
            fields=fields(),
            now=100,
            idempotency_key=None,
        )
        self.assertEqual(created.author_user_id, 77)
        self.assertEqual(created.status, SubmissionStatus.DRAFT)
        self.assertEqual(created.version, 1)
        self.assertEqual(created.revision.revision_number, 1)
        self.assertEqual(created.revision.title, "Первая работа")
        self.assertEqual(created.current_draft_revision_id, created.revision.id)

        listing = await self.storage.list_for_author(77)
        self.assertEqual(len(listing), 1)
        self.assertEqual(listing[0].id, created.id)
        self.assertEqual(listing[0].title, "Первая работа")

        loaded = await self.storage.get_for_author(created.id, 77)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.id, created.id)

        updated = await self.storage.update_draft(
            submission_id=created.id,
            author_user_id=77,
            expected_version=1,
            fields=fields("Новая версия названия"),
            now=101,
        )
        self.assertEqual(updated.version, 2)
        self.assertEqual(updated.revision.title, "Новая версия названия")
        self.assertEqual(updated.updated_at, 101)

    async def test_uuid_does_not_bypass_author_ownership(self):
        created = await self.storage.create_submission(
            author_user_id=77,
            writers_chat_id=-1002619489118,
            fields=fields(),
            now=100,
            idempotency_key=None,
        )
        self.assertIsNone(await self.storage.get_for_author(created.id, 88))
        self.assertEqual(await self.storage.list_for_author(88), [])

    async def test_stale_expected_version_raises_conflict_without_overwrite(self):
        created = await self.storage.create_submission(
            author_user_id=77,
            writers_chat_id=-1002619489118,
            fields=fields(),
            now=100,
            idempotency_key=None,
        )
        first = await self.storage.update_draft(
            submission_id=created.id,
            author_user_id=77,
            expected_version=1,
            fields=fields("Победившая запись"),
            now=101,
        )
        self.assertEqual(first.version, 2)

        with self.assertRaises(ConflictError):
            await self.storage.update_draft(
                submission_id=created.id,
                author_user_id=77,
                expected_version=1,
                fields=fields("Устаревшая запись"),
                now=102,
            )

        loaded = await self.storage.get_for_author(created.id, 77)
        self.assertEqual(loaded.revision.title, "Победившая запись")
        self.assertEqual(loaded.version, 2)

    async def test_draft_survives_storage_recreation(self):
        created = await self.storage.create_submission(
            author_user_id=77,
            writers_chat_id=-1002619489118,
            fields=fields(),
            now=100,
            idempotency_key=None,
        )
        await self.storage.close()

        reopened = PostgresWritersSubmissionStorage(TEST_DATABASE_URL)
        await reopened.initialize()
        try:
            loaded = await reopened.get_for_author(created.id, 77)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.id, created.id)
            self.assertEqual(loaded.revision.title, "Первая работа")
        finally:
            await reopened.close()

        # Prevent tearDown from closing the already-closed original pool twice.
        self.storage = PostgresWritersSubmissionStorage(TEST_DATABASE_URL)


    async def test_create_idempotency_survives_storage_recreation(self):
        first = await self.storage.create_submission(
            author_user_id=77,
            writers_chat_id=-1002619489118,
            fields=fields("Оригинал"),
            now=100,
            idempotency_key="persistent-create",
        )
        await self.storage.close()

        reopened = PostgresWritersSubmissionStorage(TEST_DATABASE_URL)
        await reopened.initialize()
        try:
            second = await reopened.create_submission(
                author_user_id=77,
                writers_chat_id=-1002619489118,
                fields=fields("Дубликат не должен победить"),
                now=200,
                idempotency_key="persistent-create",
            )
            self.assertEqual(second.id, first.id)
            self.assertEqual(second.revision.id, first.revision.id)
            self.assertEqual(second.revision.title, "Оригинал")
            count = await reopened.pool.fetchval(
                "SELECT COUNT(*) FROM writers_submissions WHERE author_user_id = 77"
            )
            self.assertEqual(count, 1)
        finally:
            await reopened.close()

        self.storage = PostgresWritersSubmissionStorage(TEST_DATABASE_URL)

    async def test_idempotency_key_is_scoped_by_actor(self):
        first = await self.storage.create_submission(
            author_user_id=77,
            writers_chat_id=-1002619489118,
            fields=fields("Первый"),
            now=100,
            idempotency_key="same-key",
        )
        second = await self.storage.create_submission(
            author_user_id=88,
            writers_chat_id=-1002619489118,
            fields=fields("Второй"),
            now=101,
            idempotency_key="same-key",
        )

        self.assertNotEqual(first.id, second.id)

    async def test_changes_requested_creates_prefilled_revision_two_idempotently(self):
        created = await self.storage.create_submission(
            author_user_id=77,
            writers_chat_id=-1002619489118,
            fields=fields("Редакция один"),
            now=100,
            idempotency_key="create-for-revision",
        )
        assert self.storage.pool is not None
        await self.storage.pool.execute(
            """
            UPDATE writers_submissions
            SET status = 'CHANGES_REQUESTED',
                current_submitted_revision_id = current_draft_revision_id,
                current_draft_revision_id = NULL,
                version = version + 1,
                updated_at = 105
            WHERE id = $1
            """,
            created.id,
        )
        await self.storage.pool.execute(
            """
            UPDATE writers_submission_revisions
            SET state = 'SEALED', sealed_at = 105
            WHERE id = $1
            """,
            created.revision.id,
        )

        revised = await self.storage.create_revision(
            submission_id=created.id,
            author_user_id=77,
            idempotency_key="revision-two",
            now=110,
        )
        duplicate = await self.storage.create_revision(
            submission_id=created.id,
            author_user_id=77,
            idempotency_key="revision-two",
            now=111,
        )

        self.assertEqual(revised.id, created.id)
        self.assertEqual(revised.status, SubmissionStatus.DRAFT)
        self.assertEqual(revised.revision.revision_number, 2)
        self.assertEqual(revised.revision.title, "Редакция один")
        self.assertEqual(duplicate.revision.id, revised.revision.id)

        rows = await self.storage.pool.fetch(
            """
            SELECT revision_number, state, title
            FROM writers_submission_revisions
            WHERE submission_id = $1
            ORDER BY revision_number
            """,
            created.id,
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["revision_number"], 1)
        self.assertEqual(rows[0]["state"], "SEALED")
        self.assertEqual(rows[0]["title"], "Редакция один")
        self.assertEqual(rows[1]["revision_number"], 2)
        self.assertEqual(rows[1]["state"], "DRAFT")

    async def test_withdraw_is_idempotent_and_terminal(self):
        created = await self.storage.create_submission(
            author_user_id=77,
            writers_chat_id=-1002619489118,
            fields=fields(),
            now=100,
            idempotency_key="create-withdraw",
        )
        first = await self.storage.withdraw_submission(
            submission_id=created.id,
            author_user_id=77,
            idempotency_key="withdraw-once",
            now=101,
        )
        second = await self.storage.withdraw_submission(
            submission_id=created.id,
            author_user_id=77,
            idempotency_key="withdraw-once",
            now=102,
        )

        self.assertEqual(first.status, SubmissionStatus.WITHDRAWN)
        self.assertEqual(second.status, SubmissionStatus.WITHDRAWN)
        self.assertEqual(first.version, second.version)

        with self.assertRaises(ConflictError):
            await self.storage.withdraw_submission(
                submission_id=created.id,
                author_user_id=77,
                idempotency_key="different-withdraw",
                now=103,
            )


if __name__ == "__main__":
    unittest.main()

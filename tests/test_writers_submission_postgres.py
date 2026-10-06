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


if __name__ == "__main__":
    unittest.main()

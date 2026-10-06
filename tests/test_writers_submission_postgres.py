from __future__ import annotations

import asyncio
import os
import unittest

from writers_submission.models import (
    ConflictError,
    NotFoundError,
    SubmissionStatus,
    ValidationError,
)
from writers_submission.storage import PostgresWritersSubmissionStorage
from writers_submission.uploads import NormalizedSubmissionFields, ValidatedUpload

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


    async def test_ready_file_is_owner_scoped_and_survives_restart(self):
        created = await self.storage.create_submission(
            author_user_id=77,
            writers_chat_id=-1002619489118,
            fields=fields(),
            now=100,
            idempotency_key="create-file-persist",
        )
        context = await self.storage.get_draft_file_context(
            submission_id=created.id,
            author_user_id=77,
        )
        upload = ValidatedUpload(
            safe_filename="story.txt",
            file_class="txt",
            declared_mime="text/plain",
            byte_size=12,
            sha256="a" * 64,
        )
        stored = await self.storage.add_ready_file(
            submission_id=created.id,
            author_user_id=77,
            revision_id=context.revision_id,
            upload=upload,
            telegram_file_id="telegram-file",
            telegram_file_unique_id="unique-file",
            storage_chat_id=-100222,
            storage_message_id=555,
            max_files=3,
            now=101,
        )
        self.assertEqual(stored.revision_id, context.revision_id)

        with self.assertRaises(NotFoundError):
            await self.storage.list_files_for_author(
                submission_id=created.id,
                author_user_id=88,
            )

        await self.storage.close()
        reopened = PostgresWritersSubmissionStorage(TEST_DATABASE_URL)
        await reopened.initialize()
        try:
            files = await reopened.list_files_for_author(
                submission_id=created.id,
                author_user_id=77,
            )
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].id, stored.id)
            self.assertEqual(files[0].telegram_file_id, "telegram-file")
        finally:
            await reopened.close()
        self.storage = PostgresWritersSubmissionStorage(TEST_DATABASE_URL)

    async def test_file_count_limit_is_rechecked_transactionally(self):
        created = await self.storage.create_submission(
            author_user_id=77,
            writers_chat_id=-1002619489118,
            fields=fields(),
            now=100,
            idempotency_key="create-file-limit",
        )
        context = await self.storage.get_draft_file_context(
            submission_id=created.id,
            author_user_id=77,
        )
        upload = ValidatedUpload(
            safe_filename="story.txt",
            file_class="txt",
            declared_mime="text/plain",
            byte_size=12,
            sha256="b" * 64,
        )
        await self.storage.add_ready_file(
            submission_id=created.id,
            author_user_id=77,
            revision_id=context.revision_id,
            upload=upload,
            telegram_file_id="file-1",
            telegram_file_unique_id="unique-1",
            storage_chat_id=-100222,
            storage_message_id=1,
            max_files=1,
            now=101,
        )
        with self.assertRaises(ValidationError):
            await self.storage.add_ready_file(
                submission_id=created.id,
                author_user_id=77,
                revision_id=context.revision_id,
                upload=upload,
                telegram_file_id="file-2",
                telegram_file_unique_id="unique-2",
                storage_chat_id=-100222,
                storage_message_id=2,
                max_files=1,
                now=102,
            )

    async def test_attachment_cannot_cross_revision_or_mutate_sealed_revision(self):
        first = await self.storage.create_submission(
            author_user_id=77,
            writers_chat_id=-1002619489118,
            fields=fields("Первая"),
            now=100,
            idempotency_key="create-first-file-scope",
        )
        second = await self.storage.create_submission(
            author_user_id=77,
            writers_chat_id=-1002619489118,
            fields=fields("Вторая"),
            now=101,
            idempotency_key="create-second-file-scope",
        )
        first_context = await self.storage.get_draft_file_context(
            submission_id=first.id,
            author_user_id=77,
        )
        second_context = await self.storage.get_draft_file_context(
            submission_id=second.id,
            author_user_id=77,
        )
        upload = ValidatedUpload(
            safe_filename="story.txt",
            file_class="txt",
            declared_mime="text/plain",
            byte_size=12,
            sha256="c" * 64,
        )
        with self.assertRaises(ConflictError):
            await self.storage.add_ready_file(
                submission_id=first.id,
                author_user_id=77,
                revision_id=second_context.revision_id,
                upload=upload,
                telegram_file_id="wrong-revision",
                telegram_file_unique_id=None,
                storage_chat_id=-100222,
                storage_message_id=3,
                max_files=3,
                now=102,
            )

        stored = await self.storage.add_ready_file(
            submission_id=first.id,
            author_user_id=77,
            revision_id=first_context.revision_id,
            upload=upload,
            telegram_file_id="sealed-file",
            telegram_file_unique_id=None,
            storage_chat_id=-100222,
            storage_message_id=4,
            max_files=3,
            now=103,
        )
        assert self.storage.pool is not None
        await self.storage.pool.execute(
            """
            UPDATE writers_submission_revisions
            SET state = 'SEALED', sealed_at = 104
            WHERE id = $1
            """,
            first_context.revision_id,
        )
        await self.storage.pool.execute(
            """
            UPDATE writers_submissions
            SET status = 'SUBMITTED',
                current_submitted_revision_id = current_draft_revision_id,
                current_draft_revision_id = NULL
            WHERE id = $1
            """,
            first.id,
        )

        with self.assertRaises(ConflictError):
            await self.storage.delete_ready_file(
                submission_id=first.id,
                author_user_id=77,
                file_id=stored.id,
                now=105,
            )


    async def test_submit_seals_revision_and_creates_one_moderation_outbox_atomically(self):
        created = await self.storage.create_submission(
            author_user_id=77,
            writers_chat_id=-1002619489118,
            fields=fields("Готовая работа"),
            now=100,
            idempotency_key="create-submit-atomic",
        )

        submitted = await self.storage.seal_and_submit(
            submission_id=created.id,
            author_user_id=77,
            expected_version=1,
            idempotency_key="submit-atomic",
            now=110,
        )
        duplicate = await self.storage.seal_and_submit(
            submission_id=created.id,
            author_user_id=77,
            expected_version=1,
            idempotency_key="submit-atomic",
            now=111,
        )

        self.assertEqual(submitted.status, SubmissionStatus.SUBMITTED)
        self.assertEqual(submitted.revision.state.value, "SEALED")
        self.assertIsNone(submitted.current_draft_revision_id)
        self.assertEqual(
            submitted.current_submitted_revision_id,
            submitted.revision.id,
        )
        self.assertEqual(duplicate.id, submitted.id)
        self.assertEqual(duplicate.revision.id, submitted.revision.id)

        assert self.storage.pool is not None
        outbox = await self.storage.pool.fetch(
            """
            SELECT event_type, state, submission_id, revision_id, dedupe_key
            FROM writers_submission_outbox
            WHERE submission_id = $1
            """,
            created.id,
        )
        self.assertEqual(len(outbox), 1)
        self.assertEqual(outbox[0]["event_type"], "MODERATION_CARD")
        self.assertEqual(outbox[0]["state"], "PENDING")
        self.assertEqual(outbox[0]["revision_id"], submitted.revision.id)
        self.assertEqual(
            outbox[0]["dedupe_key"],
            f"moderation:{created.id}:{submitted.revision.id}",
        )

    async def test_submit_rejects_empty_work_without_ready_file(self):
        empty = NormalizedSubmissionFields(
            title="Метаданные без работы",
            work_type="Рассказ",
            genre="Драма",
            description="Описание",
            body_text="",
            external_url=None,
        )
        created = await self.storage.create_submission(
            author_user_id=77,
            writers_chat_id=-1002619489118,
            fields=empty,
            now=100,
            idempotency_key="create-empty-work",
        )

        with self.assertRaises(ValidationError):
            await self.storage.seal_and_submit(
                submission_id=created.id,
                author_user_id=77,
                expected_version=1,
                idempotency_key="submit-empty-work",
                now=110,
            )

        loaded = await self.storage.get_for_author(created.id, 77)
        self.assertEqual(loaded.status, SubmissionStatus.DRAFT)
        self.assertEqual(loaded.revision.state.value, "DRAFT")

    async def test_file_only_work_can_submit_when_attachment_is_ready(self):
        empty = NormalizedSubmissionFields(
            title="Работа файлом",
            work_type="Роман",
            genre="Фантастика",
            description="Описание",
            body_text="",
            external_url=None,
        )
        created = await self.storage.create_submission(
            author_user_id=77,
            writers_chat_id=-1002619489118,
            fields=empty,
            now=100,
            idempotency_key="create-file-only",
        )
        context = await self.storage.get_draft_file_context(
            submission_id=created.id,
            author_user_id=77,
        )
        upload = ValidatedUpload(
            safe_filename="novel.pdf",
            file_class="pdf",
            declared_mime="application/pdf",
            byte_size=100,
            sha256="d" * 64,
        )
        await self.storage.add_ready_file(
            submission_id=created.id,
            author_user_id=77,
            revision_id=context.revision_id,
            upload=upload,
            telegram_file_id="ready-file-id",
            telegram_file_unique_id="ready-unique-id",
            storage_chat_id=-100222,
            storage_message_id=11,
            max_files=3,
            now=101,
        )
        current = await self.storage.get_for_author(created.id, 77)

        submitted = await self.storage.seal_and_submit(
            submission_id=created.id,
            author_user_id=77,
            expected_version=current.version,
            idempotency_key="submit-file-only",
            now=110,
        )

        self.assertEqual(submitted.status, SubmissionStatus.SUBMITTED)

    async def test_submit_rejects_corrupt_attachment_without_telegram_file_id(self):
        empty = NormalizedSubmissionFields(
            title="Поврежденное вложение",
            work_type="Рассказ",
            genre="Драма",
            description="Описание",
            body_text="",
            external_url=None,
        )
        created = await self.storage.create_submission(
            author_user_id=77,
            writers_chat_id=-1002619489118,
            fields=empty,
            now=100,
            idempotency_key="create-corrupt-file",
        )
        assert self.storage.pool is not None
        await self.storage.pool.execute(
            """
            INSERT INTO writers_submission_files(
                id, submission_id, revision_id, safe_filename, declared_mime,
                detected_file_class, byte_size, sha256, telegram_file_id,
                created_at
            )
            VALUES(
                gen_random_uuid(), $1, $2, 'broken.txt', 'text/plain',
                'txt', 5, $3, '', 101
            )
            """,
            created.id,
            created.revision.id,
            "e" * 64,
        )

        with self.assertRaises(ValidationError):
            await self.storage.seal_and_submit(
                submission_id=created.id,
                author_user_id=77,
                expected_version=1,
                idempotency_key="submit-corrupt-file",
                now=110,
            )

    async def test_submitted_revision_and_files_are_immutable(self):
        created = await self.storage.create_submission(
            author_user_id=77,
            writers_chat_id=-1002619489118,
            fields=fields(),
            now=100,
            idempotency_key="create-immutable-submit",
        )
        context = await self.storage.get_draft_file_context(
            submission_id=created.id,
            author_user_id=77,
        )
        upload = ValidatedUpload(
            safe_filename="story.txt",
            file_class="txt",
            declared_mime="text/plain",
            byte_size=12,
            sha256="f" * 64,
        )
        stored_file = await self.storage.add_ready_file(
            submission_id=created.id,
            author_user_id=77,
            revision_id=context.revision_id,
            upload=upload,
            telegram_file_id="immutable-file",
            telegram_file_unique_id=None,
            storage_chat_id=-100222,
            storage_message_id=12,
            max_files=3,
            now=101,
        )
        current = await self.storage.get_for_author(created.id, 77)
        submitted = await self.storage.seal_and_submit(
            submission_id=created.id,
            author_user_id=77,
            expected_version=current.version,
            idempotency_key="submit-immutable",
            now=110,
        )

        with self.assertRaises(ConflictError):
            await self.storage.update_draft(
                submission_id=created.id,
                author_user_id=77,
                expected_version=submitted.version,
                fields=fields("Запрещенная правка"),
                now=111,
            )
        with self.assertRaises(ConflictError):
            await self.storage.delete_ready_file(
                submission_id=created.id,
                author_user_id=77,
                file_id=stored_file.id,
                now=112,
            )

    async def test_concurrent_duplicate_submit_creates_one_outbox_row(self):
        created = await self.storage.create_submission(
            author_user_id=77,
            writers_chat_id=-1002619489118,
            fields=fields(),
            now=100,
            idempotency_key="create-concurrent-submit",
        )

        first, second = await asyncio.gather(
            self.storage.seal_and_submit(
                submission_id=created.id,
                author_user_id=77,
                expected_version=1,
                idempotency_key="same-submit-key",
                now=110,
            ),
            self.storage.seal_and_submit(
                submission_id=created.id,
                author_user_id=77,
                expected_version=1,
                idempotency_key="same-submit-key",
                now=110,
            ),
        )

        self.assertEqual(first.id, second.id)
        self.assertEqual(first.revision.id, second.revision.id)
        assert self.storage.pool is not None
        count = await self.storage.pool.fetchval(
            """
            SELECT COUNT(*)
            FROM writers_submission_outbox
            WHERE submission_id = $1 AND event_type = 'MODERATION_CARD'
            """,
            created.id,
        )
        self.assertEqual(count, 1)

    async def test_outbox_claim_and_result_transitions_are_restart_safe(self):
        created = await self.storage.create_submission(
            author_user_id=77,
            writers_chat_id=-1002619489118,
            fields=fields(),
            now=100,
            idempotency_key="create-outbox-api",
        )
        await self.storage.seal_and_submit(
            submission_id=created.id,
            author_user_id=77,
            expected_version=1,
            idempotency_key="submit-outbox-api",
            now=110,
        )

        claimed = await self.storage.claim_due_outbox(
            worker_id="worker-a",
            now=111,
            lease_seconds=60,
            limit=10,
        )
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0].state.value, "IN_FLIGHT")
        self.assertEqual(claimed[0].worker_id, "worker-a")

        await self.storage.mark_outbox_retryable(
            outbox_id=claimed[0].id,
            worker_id="worker-a",
            now=112,
            next_attempt_at=120,
            error_code="telegram_retry",
        )
        self.assertEqual(
            await self.storage.claim_due_outbox(
                worker_id="worker-b",
                now=119,
                lease_seconds=60,
                limit=10,
            ),
            [],
        )
        retried = await self.storage.claim_due_outbox(
            worker_id="worker-b",
            now=120,
            lease_seconds=60,
            limit=10,
        )
        self.assertEqual(len(retried), 1)
        await self.storage.mark_outbox_delivered(
            outbox_id=retried[0].id,
            worker_id="worker-b",
            now=121,
        )
        self.assertEqual(
            await self.storage.claim_due_outbox(
                worker_id="worker-c",
                now=1000,
                lease_seconds=60,
                limit=10,
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()

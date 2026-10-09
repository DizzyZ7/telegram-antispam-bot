from __future__ import annotations

import asyncio
import json
import os
import unittest

from writers_submission.models import (
    ConflictError,
    NotFoundError,
    ReviewAction,
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

    async def test_ficbook_form_fields_survive_restart_and_submission(self):
        form = NormalizedSubmissionFields(
            title="Легенда",
            work_type="Оридж",
            genre="Джен",
            description="Описание",
            body_text="",
            external_url="https://ficbook.net/readfic/555",
            details={
                "form_version": 2,
                "fandom": "",
                "size_category": "макси",
                "rating": "R",
                "completion": "завершен",
                "size_words": 123000,
                "pages": 210,
                "parts": 30,
                "extra_links": ["https://t.me/example"],
                "visual_mode": "palette",
                "palette_colors": ["#123456", "#223344", "#334455", "#445566"],
            },
        )
        draft = await self.storage.create_submission(
            author_user_id=77,
            writers_chat_id=-1002619489118,
            fields=form,
            now=100,
            idempotency_key="ficbook-draft",
        )
        self.assertEqual(draft.revision.details["size_category"], "макси")
        self.assertEqual(draft.revision.details["palette_colors"][0], "#123456")
        await self.storage.close()

        reopened = PostgresWritersSubmissionStorage(TEST_DATABASE_URL)
        await reopened.initialize()
        self.storage = reopened
        loaded = await reopened.get_for_author(draft.id, 77)
        self.assertEqual(loaded.revision.details["parts"], 30)
        self.assertEqual(loaded.revision.external_url, "https://ficbook.net/readfic/555")
        sealed = await reopened.seal_and_submit(
            submission_id=draft.id,
            author_user_id=77,
            expected_version=loaded.version,
            idempotency_key="ficbook-submit",
            now=110,
        )
        self.assertEqual(sealed.status, SubmissionStatus.SUBMITTED)
        moderation = await reopened.get_moderation_delivery_context(
            submission_id=draft.id,
            revision_id=sealed.revision.id,
        )
        self.assertEqual(moderation.details["rating"], "R")
        self.assertEqual(moderation.details["extra_links"], ["https://t.me/example"])

    async def test_ficbook_form_requires_image_when_image_mode_selected(self):
        form = NormalizedSubmissionFields(
            title="Работа",
            work_type="Оридж",
            genre="Джен",
            description="Описание",
            body_text="",
            external_url="https://ficbook.net/readfic/555",
            details={
                "form_version": 2,
                "fandom": "",
                "size_category": "мини",
                "rating": "G",
                "completion": "в процессе",
                "size_words": None, "pages": None, "parts": None,
                "extra_links": [],
                "visual_mode": "image",
                "palette_colors": [],
            },
        )
        draft = await self.storage.create_submission(
            author_user_id=77, writers_chat_id=-1002619489118,
            fields=form, now=100, idempotency_key="ficbook-image",
        )
        with self.assertRaisesRegex(ValidationError, "illustration"):
            await self.storage.seal_and_submit(
                submission_id=draft.id, author_user_id=77,
                expected_version=draft.version,
                idempotency_key="ficbook-image-submit", now=101,
            )

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


    async def test_old_file_class_constraint_migrates_and_preserves_attachments(self):
        # Reproduce a real production installation originally created before
        # JPG/PNG were added to the form. CREATE TABLE IF NOT EXISTS alone
        # does not change that PostgreSQL CHECK constraint.
        draft = await self.storage.create_submission(
            author_user_id=77,
            writers_chat_id=-1002619489118,
            fields=fields("Старый черновик"),
            now=100,
            idempotency_key="photo-migration-draft",
        )
        old_file = await self.storage.add_ready_file(
            submission_id=draft.id,
            author_user_id=77,
            revision_id=draft.revision.id,
            upload=ValidatedUpload(
                safe_filename="story.txt",
                file_class="txt",
                declared_mime="text/plain",
                byte_size=8,
                sha256="a" * 64,
            ),
            telegram_file_id="old-story",
            telegram_file_unique_id=None,
            storage_chat_id=-100222,
            storage_message_id=101,
            max_files=3,
            now=101,
        )
        assert self.storage.pool is not None
        async with self.storage.pool.acquire() as conn:
            await conn.execute(
                "ALTER TABLE writers_submission_files "
                "DROP CONSTRAINT writers_submission_file_class_check"
            )
            await conn.execute(
                "ALTER TABLE writers_submission_files "
                "ADD CONSTRAINT writers_submission_file_class_check "
                "CHECK (detected_file_class IN ('pdf', 'docx', 'txt'))"
            )

        await self.storage.close()
        migrated = PostgresWritersSubmissionStorage(TEST_DATABASE_URL)
        await migrated.initialize()
        self.storage = migrated
        assert migrated.pool is not None
        definition = await migrated.pool.fetchval(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid='writers_submission_files'::regclass "
            "AND conname='writers_submission_file_class_check'"
        )
        self.assertIn("png", str(definition))
        self.assertIn("jpeg", str(definition))
        for index, (file_class, name, mime) in enumerate((
            ("png", "cover.png", "image/png"),
            ("jpeg", "cover.jpg", "image/jpeg"),
        ), start=1):
            await migrated.add_ready_file(
                submission_id=draft.id,
                author_user_id=77,
                revision_id=draft.revision.id,
                upload=ValidatedUpload(
                    safe_filename=name,
                    file_class=file_class,
                    declared_mime=mime,
                    byte_size=512,
                    sha256=(str(index) * 64),
                ),
                telegram_file_id=f"photo-{index}",
                telegram_file_unique_id=None,
                storage_chat_id=-100222,
                storage_message_id=101 + index,
                max_files=3,
                now=101 + index,
            )
        files = await migrated.list_files_for_author(
            submission_id=draft.id, author_user_id=77
        )
        self.assertEqual({f.detected_file_class for f in files}, {"txt", "png", "jpeg"})
        self.assertIn(old_file.id, {f.id for f in files})

        # A second startup is idempotent and also leaves all photos intact.
        await migrated.close()
        again = PostgresWritersSubmissionStorage(TEST_DATABASE_URL)
        await again.initialize()
        self.storage = again
        files = await again.list_files_for_author(
            submission_id=draft.id, author_user_id=77
        )
        self.assertEqual(len(files), 3)

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
              AND event_type = 'MODERATION_CARD'
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

        acceptance = await self.storage.pool.fetchrow(
            """
            SELECT event_type, state, payload_json, dedupe_key
            FROM writers_submission_outbox
            WHERE submission_id = $1
              AND revision_id = $2
              AND dedupe_key = $3
            """,
            created.id,
            submitted.revision.id,
            f"author:{created.id}:{submitted.revision.id}:SUBMISSION_ACCEPTED",
        )
        self.assertIsNotNone(acceptance)
        self.assertEqual(acceptance["event_type"], "AUTHOR_NOTIFICATION")
        self.assertEqual(acceptance["state"], "PENDING")
        self.assertEqual(
            json.loads(acceptance["payload_json"])["kind"],
            "SUBMISSION_ACCEPTED",
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
        self.assertEqual(len(claimed), 2)
        target = next(
            item for item in claimed
            if item.event_type.value == "MODERATION_CARD"
        )
        accepted = next(
            item for item in claimed
            if item.event_type.value == "AUTHOR_NOTIFICATION"
        )
        self.assertEqual(target.state.value, "IN_FLIGHT")
        self.assertEqual(target.worker_id, "worker-a")
        await self.storage.mark_outbox_delivered(
            outbox_id=accepted.id,
            worker_id="worker-a",
            now=112,
        )

        await self.storage.mark_outbox_retryable(
            outbox_id=target.id,
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


    async def test_concurrent_moderator_claim_has_exactly_one_winner(self):
        created = await self.storage.create_submission(
            author_user_id=77,
            writers_chat_id=-1002619489118,
            fields=fields("Конкурентная модерация"),
            now=100,
            idempotency_key="create-claim-race",
        )
        submitted = await self.storage.seal_and_submit(
            submission_id=created.id,
            author_user_id=77,
            expected_version=1,
            idempotency_key="submit-claim-race",
            now=110,
        )

        first, second = await asyncio.gather(
            self.storage.claim_submission(
                submission_id=submitted.id,
                revision_id=submitted.revision.id,
                reviewer_user_id=9001,
                now=120,
            ),
            self.storage.claim_submission(
                submission_id=submitted.id,
                revision_id=submitted.revision.id,
                reviewer_user_id=9002,
                now=120,
            ),
            return_exceptions=True,
        )

        results = [value for value in (first, second) if not isinstance(value, Exception)]
        failures = [value for value in (first, second) if isinstance(value, Exception)]
        self.assertEqual(len(results), 1)
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], ConflictError)
        self.assertEqual(results[0].submission.status, SubmissionStatus.IN_REVIEW)
        self.assertIn(results[0].submission.claimed_by_user_id, {9001, 9002})

    async def test_decision_and_withdraw_race_has_one_legal_winner(self):
        created = await self.storage.create_submission(
            author_user_id=77,
            writers_chat_id=-1002619489118,
            fields=fields("Решение против отзыва"),
            now=100,
            idempotency_key="create-decision-withdraw-race",
        )
        submitted = await self.storage.seal_and_submit(
            submission_id=created.id,
            author_user_id=77,
            expected_version=1,
            idempotency_key="submit-decision-withdraw-race",
            now=110,
        )
        await self.storage.claim_submission(
            submission_id=submitted.id,
            revision_id=submitted.revision.id,
            reviewer_user_id=9001,
            now=120,
        )

        decision, withdrawal = await asyncio.gather(
            self.storage.decide_submission(
                submission_id=submitted.id,
                revision_id=submitted.revision.id,
                reviewer_user_id=9001,
                action=ReviewAction.APPROVE,
                comment=None,
                now=130,
            ),
            self.storage.withdraw_submission(
                submission_id=submitted.id,
                author_user_id=77,
                idempotency_key="withdraw-race",
                now=130,
            ),
            return_exceptions=True,
        )

        results = [value for value in (decision, withdrawal) if not isinstance(value, Exception)]
        failures = [value for value in (decision, withdrawal) if isinstance(value, Exception)]
        self.assertEqual(len(results), 1)
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], ConflictError)

        loaded = await self.storage.get_for_author(submitted.id, 77)
        self.assertIn(
            loaded.status,
            {SubmissionStatus.APPROVED, SubmissionStatus.WITHDRAWN},
        )

    async def test_duplicate_decision_creates_one_author_notification(self):
        created = await self.storage.create_submission(
            author_user_id=77,
            writers_chat_id=-1002619489118,
            fields=fields("Один notification"),
            now=100,
            idempotency_key="create-one-notification",
        )
        submitted = await self.storage.seal_and_submit(
            submission_id=created.id,
            author_user_id=77,
            expected_version=1,
            idempotency_key="submit-one-notification",
            now=110,
        )
        await self.storage.claim_submission(
            submission_id=submitted.id,
            revision_id=submitted.revision.id,
            reviewer_user_id=9001,
            now=120,
        )
        first = await self.storage.decide_submission(
            submission_id=submitted.id,
            revision_id=submitted.revision.id,
            reviewer_user_id=9001,
            action=ReviewAction.REQUEST_CHANGES,
            comment="Усилить финал",
            now=130,
        )
        duplicate = await self.storage.decide_submission(
            submission_id=submitted.id,
            revision_id=submitted.revision.id,
            reviewer_user_id=9001,
            action=ReviewAction.REQUEST_CHANGES,
            comment="Усилить финал",
            now=131,
        )

        self.assertTrue(first.applied)
        self.assertFalse(duplicate.applied)
        assert self.storage.pool is not None
        count = await self.storage.pool.fetchval(
            """
            SELECT COUNT(*)
            FROM writers_submission_outbox
            WHERE submission_id = $1
              AND revision_id = $2
              AND event_type = 'AUTHOR_NOTIFICATION'
              AND dedupe_key = $3
            """,
            submitted.id,
            submitted.revision.id,
            f"author:{submitted.id}:{submitted.revision.id}:REQUEST_CHANGES",
        )
        self.assertEqual(count, 1)

        context = await self.storage.get_author_notification_context(
            submission_id=submitted.id,
            revision_id=submitted.revision.id,
        )
        self.assertEqual(context.author_user_id, 77)
        self.assertEqual(context.action, ReviewAction.REQUEST_CHANGES)
        self.assertEqual(context.comment, "Усилить финал")

    async def test_approval_enqueues_one_owner_only_preview_atomically(self):
        created = await self.storage.create_submission(
            author_user_id=77, writers_chat_id=-1002619489118,
            fields=fields("Пост для владельца ИКФ"), now=100,
            idempotency_key="owner-preview-create",
        )
        submitted = await self.storage.seal_and_submit(
            submission_id=created.id, author_user_id=77,
            expected_version=created.version,
            idempotency_key="owner-preview-submit", now=110,
        )
        await self.storage.claim_submission(
            submission_id=submitted.id, revision_id=submitted.revision.id,
            reviewer_user_id=9001, now=120,
        )
        first = await self.storage.decide_submission(
            submission_id=submitted.id, revision_id=submitted.revision.id,
            reviewer_user_id=9001, action=ReviewAction.APPROVE,
            comment=None, now=130,
        )
        repeated = await self.storage.decide_submission(
            submission_id=submitted.id, revision_id=submitted.revision.id,
            reviewer_user_id=9001, action=ReviewAction.APPROVE,
            comment=None, now=140,
        )
        self.assertTrue(first.applied)
        self.assertFalse(repeated.applied)
        assert self.storage.pool is not None
        rows = await self.storage.pool.fetch(
            "SELECT event_type, dedupe_key FROM writers_submission_outbox "
            "WHERE submission_id=$1 ORDER BY created_at, event_type",
            submitted.id,
        )
        owner = [r for r in rows if r["event_type"] == "OWNER_PREVIEW"]
        self.assertEqual(len(owner), 1)
        self.assertEqual(owner[0]["dedupe_key"], f"owner:{submitted.id}:{submitted.revision.id}:APPROVED")
        self.assertEqual(
            len([r for r in rows if r["event_type"] == "AUTHOR_NOTIFICATION"]),
            2,  # submission received + author decision
        )

    async def test_rejected_submission_has_no_owner_preview(self):
        created = await self.storage.create_submission(
            author_user_id=77, writers_chat_id=-1002619489118,
            fields=fields("Не одобрять"), now=100,
            idempotency_key="no-owner-create",
        )
        submitted = await self.storage.seal_and_submit(
            submission_id=created.id, author_user_id=77,
            expected_version=created.version, idempotency_key="no-owner-submit",
            now=110,
        )
        await self.storage.claim_submission(
            submission_id=submitted.id, revision_id=submitted.revision.id,
            reviewer_user_id=9001, now=120,
        )
        await self.storage.decide_submission(
            submission_id=submitted.id, revision_id=submitted.revision.id,
            reviewer_user_id=9001, action=ReviewAction.REJECT, comment=None, now=130,
        )
        count = await self.storage.pool.fetchval(
            "SELECT COUNT(*) FROM writers_submission_outbox "
            "WHERE submission_id=$1 AND event_type='OWNER_PREVIEW'",
            submitted.id,
        )
        self.assertEqual(count, 0)

    async def test_custom_cover_requires_manually_recorded_owner_payment(self):
        form = NormalizedSubmissionFields(
            title="Платная обложка",
            work_type="Оридж",
            genre="Джен",
            description="Описание заявки",
            body_text="",
            external_url="https://ficbook.net/readfic/555",
            details={
                "form_version": 2,
                "size_category": "мини",
                "rating": "G",
                "completion": "в процессе",
                "visual_mode": "image",
                "palette_colors": [],
            },
        )
        draft = await self.storage.create_submission(
            author_user_id=77, writers_chat_id=-1002619489118,
            fields=form, now=100, idempotency_key="paid-cover-create",
        )
        await self.storage.add_ready_file(
            submission_id=draft.id, author_user_id=77,
            revision_id=draft.revision.id,
            upload=ValidatedUpload(
                safe_filename="photo.jpg", file_class="jpeg",
                declared_mime="image/jpeg", byte_size=128,
                sha256="b" * 64,
            ),
            telegram_file_id="owner-paid-cover", telegram_file_unique_id=None,
            storage_chat_id=-100222, storage_message_id=200,
            max_files=3, now=105,
        )
        fresh = await self.storage.get_for_author(draft.id, 77)
        submitted = await self.storage.seal_and_submit(
            submission_id=draft.id, author_user_id=77,
            expected_version=fresh.version,
            idempotency_key="paid-cover-submit", now=110,
        )
        await self.storage.claim_submission(
            submission_id=submitted.id, revision_id=submitted.revision.id,
            reviewer_user_id=2039781854, now=120,
        )
        self.assertFalse(await self.storage.is_cover_payment_confirmed(
            submission_id=submitted.id, revision_id=submitted.revision.id,
        ))
        with self.assertRaisesRegex(ValidationError, "Paid custom cover"):
            await self.storage.decide_submission(
                submission_id=submitted.id, revision_id=submitted.revision.id,
                reviewer_user_id=2039781854, action=ReviewAction.APPROVE,
                comment=None, now=130,
            )
        self.assertEqual(
            (await self.storage.get_for_author(submitted.id, 77)).status,
            SubmissionStatus.IN_REVIEW,
        )
        self.assertTrue(await self.storage.confirm_cover_payment(
            submission_id=submitted.id, revision_id=submitted.revision.id,
            reviewer_user_id=2039781854, now=140,
        ))
        self.assertFalse(await self.storage.confirm_cover_payment(
            submission_id=submitted.id, revision_id=submitted.revision.id,
            reviewer_user_id=2039781854, now=141,
        ))
        self.assertTrue(await self.storage.is_cover_payment_confirmed(
            submission_id=submitted.id, revision_id=submitted.revision.id,
        ))
        decision = await self.storage.decide_submission(
            submission_id=submitted.id, revision_id=submitted.revision.id,
            reviewer_user_id=2039781854, action=ReviewAction.APPROVE,
            comment=None, now=150,
        )
        self.assertEqual(decision.submission.status, SubmissionStatus.APPROVED)
        assert self.storage.pool is not None
        receipts = await self.storage.pool.fetchval(
            "SELECT COUNT(*) FROM writers_submission_cover_payments WHERE submission_id=$1",
            submitted.id,
        )
        self.assertEqual(receipts, 1)

    async def test_stale_inflight_outbox_lease_becomes_claimable_again(self):
        created = await self.storage.create_submission(
            author_user_id=77,
            writers_chat_id=-1002619489118,
            fields=fields("Lease recovery"),
            now=100,
            idempotency_key="create-lease-recovery",
        )
        await self.storage.seal_and_submit(
            submission_id=created.id,
            author_user_id=77,
            expected_version=1,
            idempotency_key="submit-lease-recovery",
            now=110,
        )
        first = await self.storage.claim_due_outbox(
            worker_id="worker-a",
            now=120,
            lease_seconds=60,
            limit=10,
        )
        self.assertEqual(len(first), 2)
        target = next(
            item for item in first
            if item.event_type.value == "MODERATION_CARD"
        )
        accepted = next(
            item for item in first
            if item.event_type.value == "AUTHOR_NOTIFICATION"
        )
        await self.storage.mark_outbox_delivered(
            outbox_id=accepted.id,
            worker_id="worker-a",
            now=121,
        )

        before_expiry = await self.storage.claim_due_outbox(
            worker_id="worker-b",
            now=179,
            lease_seconds=60,
            limit=10,
        )
        self.assertEqual(before_expiry, [])

        reclaimed = await self.storage.claim_due_outbox(
            worker_id="worker-b",
            now=180,
            lease_seconds=60,
            limit=10,
        )
        self.assertEqual(len(reclaimed), 1)
        self.assertEqual(reclaimed[0].id, target.id)
        self.assertEqual(reclaimed[0].worker_id, "worker-b")
        self.assertEqual(reclaimed[0].attempt_count, target.attempt_count + 1)


    async def test_withdraw_creates_one_author_confirmation_notification(self):
        created = await self.storage.create_submission(
            author_user_id=77,
            writers_chat_id=-1002619489118,
            fields=fields("Отзываемая работа"),
            now=100,
            idempotency_key="create-withdraw-notification",
        )

        first = await self.storage.withdraw_submission(
            submission_id=created.id,
            author_user_id=77,
            idempotency_key="withdraw-notification",
            now=110,
        )
        duplicate = await self.storage.withdraw_submission(
            submission_id=created.id,
            author_user_id=77,
            idempotency_key="withdraw-notification",
            now=111,
        )

        self.assertEqual(first.status, SubmissionStatus.WITHDRAWN)
        self.assertEqual(duplicate.id, first.id)
        assert self.storage.pool is not None
        rows = await self.storage.pool.fetch(
            """
            SELECT event_type, payload_json, dedupe_key
            FROM writers_submission_outbox
            WHERE submission_id = $1
              AND event_type = 'AUTHOR_NOTIFICATION'
            """,
            created.id,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0]["dedupe_key"],
            f"author:{created.id}:{created.revision.id}:WITHDRAWN",
        )
        self.assertEqual(
            json.loads(rows[0]["payload_json"])["kind"],
            "WITHDRAWN",
        )

    async def test_delivered_outbox_persists_telegram_receipt_ids(self):
        created = await self.storage.create_submission(
            author_user_id=77,
            writers_chat_id=-1002619489118,
            fields=fields("Delivery receipt"),
            now=100,
            idempotency_key="create-delivery-receipt",
        )
        await self.storage.seal_and_submit(
            submission_id=created.id,
            author_user_id=77,
            expected_version=1,
            idempotency_key="submit-delivery-receipt",
            now=110,
        )
        claimed = await self.storage.claim_due_outbox(
            worker_id="worker-receipt",
            now=120,
            lease_seconds=60,
            limit=10,
        )
        moderation = next(
            item for item in claimed
            if item.event_type.value == "MODERATION_CARD"
        )

        await self.storage.mark_outbox_delivered(
            outbox_id=moderation.id,
            worker_id="worker-receipt",
            now=121,
            delivery_chat_id=-100111,
            delivery_message_ids=(501, 502),
        )

        assert self.storage.pool is not None
        row = await self.storage.pool.fetchrow(
            """
            SELECT delivery_chat_id, delivery_message_ids_json
            FROM writers_submission_outbox
            WHERE id = $1
            """,
            moderation.id,
        )
        self.assertEqual(row["delivery_chat_id"], -100111)
        self.assertEqual(
            json.loads(row["delivery_message_ids_json"]),
            [501, 502],
        )


    async def test_history_is_owned_and_restart_safe(self):
        created = await self.storage.create_submission(
            author_user_id=77,
            writers_chat_id=-1002619489118,
            fields=fields("История"),
            now=100,
            idempotency_key="history-create",
        )
        await self.storage.update_draft(
            submission_id=created.id,
            author_user_id=77,
            expected_version=1,
            fields=fields("История 2"),
            now=101,
        )

        history = await self.storage.list_history_for_author(
            submission_id=created.id,
            author_user_id=77,
        )
        self.assertGreaterEqual(len(history), 2)
        self.assertEqual(history[0]["event_type"], "DRAFT_CREATED")
        self.assertEqual(history[-1]["event_type"], "DRAFT_UPDATED")

        with self.assertRaises(NotFoundError):
            await self.storage.list_history_for_author(
                submission_id=created.id,
                author_user_id=88,
            )


if __name__ == "__main__":
    unittest.main()

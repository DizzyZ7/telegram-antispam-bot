from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from writers_submission.files import WritersFileService
from writers_submission.models import ConflictError, NotFoundError, ValidationError


def temp_file(*, suffix: str = ".txt", content: bytes = b"hello writers") -> Path:
    handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        handle.write(content)
        return Path(handle.name)
    finally:
        handle.close()


def telegram_message():
    return SimpleNamespace(
        message_id=555,
        chat=SimpleNamespace(id=-100222),
        document=SimpleNamespace(
            file_id="BQACAgIAAxkBAA-test",
            file_unique_id="AgAD-test",
        ),
    )


class WritersFileServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.revision_id = uuid4()
        self.submission_id = uuid4()
        self.persisted = SimpleNamespace(
            id=uuid4(),
            submission_id=self.submission_id,
            revision_id=self.revision_id,
            telegram_file_id="BQACAgIAAxkBAA-test",
            telegram_file_unique_id="AgAD-test",
            storage_chat_id=-100222,
            storage_message_id=555,
        )
        self.storage = SimpleNamespace(
            get_draft_file_context=AsyncMock(
                return_value=SimpleNamespace(
                    revision_id=self.revision_id,
                    file_count=0,
                )
            ),
            add_ready_file=AsyncMock(return_value=self.persisted),
            delete_ready_file=AsyncMock(return_value=self.persisted),
        )
        self.bot = SimpleNamespace(
            send_document=AsyncMock(return_value=telegram_message()),
            delete_message=AsyncMock(),
            get_chat=AsyncMock(return_value=SimpleNamespace(
                id=-100222,
                type="supergroup",
                username=None,
                active_usernames=None,
            )),
        )
        self.config = SimpleNamespace(
            file_chat_id=-100222,
            writers_chat_id=-1002619489118,
            moderation_chat_id=2039781854,
            max_files=3,
            max_file_bytes=1024 * 1024,
        )
        self.service = WritersFileService(
            self.bot,
            self.storage,
            self.config,
        )

    async def test_writers_group_is_never_used_for_staging(self):
        self.config.file_chat_id = -1002619489118
        path = temp_file()
        with self.assertRaisesRegex(ValidationError, "небезопасно"):
            await self.service.attach_from_temp(
                author_user_id=77, submission_id=self.submission_id,
                temp_path=path, original_filename="private-story.txt",
                declared_mime="text/plain", now=100,
            )
        self.bot.get_chat.assert_not_awaited()
        self.bot.send_document.assert_not_awaited()
        self.assertFalse(path.exists())

    async def test_moderation_group_is_never_used_for_staging(self):
        self.config.moderation_chat_id = -100222
        path = temp_file()
        with self.assertRaises(ValidationError):
            await self.service.attach_from_temp(
                author_user_id=77, submission_id=self.submission_id,
                temp_path=path, original_filename="private-story.txt",
                declared_mime="text/plain", now=100,
            )
        self.bot.send_document.assert_not_awaited()
        self.assertFalse(path.exists())

    async def test_public_username_disables_file_storage(self):
        for username, active_usernames in (
            ("chat_IKF", None),
            (None, ["chat_IKF"]),
        ):
            with self.subTest(username=username, active_usernames=active_usernames):
                self.bot.get_chat.return_value = SimpleNamespace(
                    id=-100222, type="supergroup",
                    username=username, active_usernames=active_usernames,
                )
                path = temp_file()
                with self.assertRaisesRegex(ValidationError, "закрытый"):
                    await self.service.attach_from_temp(
                        author_user_id=77, submission_id=self.submission_id,
                        temp_path=path, original_filename="private-story.txt",
                        declared_mime="text/plain", now=100,
                    )
                self.bot.send_document.assert_not_awaited()
                self.assertFalse(path.exists())

    async def test_wrong_chat_type_or_id_fail_closed(self):
        for chat_id, chat_type in ((-100222, "channel"), (-100999, "supergroup"),
                                  (-100222, "private")):
            with self.subTest(chat_id=chat_id, chat_type=chat_type):
                self.bot.get_chat.return_value = SimpleNamespace(
                    id=chat_id, type=chat_type, username=None,
                )
                path = temp_file()
                with self.assertRaises(ValidationError):
                    await self.service.attach_from_temp(
                        author_user_id=77, submission_id=self.submission_id,
                        temp_path=path, original_filename="private-story.txt",
                        declared_mime="text/plain", now=100,
                    )
                self.bot.send_document.assert_not_awaited()

    async def test_telegram_chat_lookup_failure_never_posts_file(self):
        self.bot.get_chat.side_effect = RuntimeError("network failure")
        path = temp_file()
        with self.assertRaisesRegex(ValidationError, "недоступно"):
            await self.service.attach_from_temp(
                author_user_id=77, submission_id=self.submission_id,
                temp_path=path, original_filename="private-story.txt",
                declared_mime="text/plain", now=100,
            )
        self.bot.send_document.assert_not_awaited()
        self.storage.add_ready_file.assert_not_awaited()
        self.assertFalse(path.exists())

    async def test_staging_rechecks_destination_for_every_file(self):
        path = temp_file()
        await self.service.attach_from_temp(
            author_user_id=77, submission_id=self.submission_id,
            temp_path=path, original_filename="story.txt",
            declared_mime="text/plain", now=100,
        )
        self.bot.get_chat.assert_awaited_once_with(-100222)
        self.bot.get_chat.return_value = SimpleNamespace(
            id=-100222, type="supergroup", username="chat_IKF",
        )
        self.bot.send_document.reset_mock()
        path = temp_file()
        with self.assertRaises(ValidationError):
            await self.service.attach_from_temp(
                author_user_id=77, submission_id=self.submission_id,
                temp_path=path, original_filename="story.txt",
                declared_mime="text/plain", now=101,
            )
        self.bot.send_document.assert_not_awaited()

    async def test_validation_happens_before_telegram_upload(self):
        path = temp_file(suffix=".exe")
        with self.assertRaises(ValidationError):
            await self.service.attach_from_temp(
                author_user_id=77,
                submission_id=self.submission_id,
                temp_path=path,
                original_filename="payload.exe",
                declared_mime="application/octet-stream",
                now=100,
            )

        self.bot.send_document.assert_not_awaited()
        self.storage.add_ready_file.assert_not_awaited()
        self.assertFalse(path.exists())

    async def test_file_count_limit_blocks_before_telegram_upload(self):
        path = temp_file()
        self.storage.get_draft_file_context.return_value = SimpleNamespace(
            revision_id=self.revision_id,
            file_count=3,
        )

        with self.assertRaisesRegex(ValidationError, "maximum"):
            await self.service.attach_from_temp(
                author_user_id=77,
                submission_id=self.submission_id,
                temp_path=path,
                original_filename="story.txt",
                declared_mime="text/plain",
                now=100,
            )

        self.bot.send_document.assert_not_awaited()
        self.storage.add_ready_file.assert_not_awaited()
        self.assertFalse(path.exists())

    async def test_successful_staging_persists_telegram_ids_then_removes_temp(self):
        path = temp_file(content="Привет".encode("utf-8"))

        result = await self.service.attach_from_temp(
            author_user_id=77,
            submission_id=self.submission_id,
            temp_path=path,
            original_filename="story.txt",
            declared_mime="text/plain",
            now=100,
        )

        self.assertEqual(result.id, self.persisted.id)
        self.bot.send_document.assert_awaited_once()
        send_kwargs = self.bot.send_document.await_args.kwargs
        self.assertEqual(send_kwargs["chat_id"], -100222)
        self.assertTrue(send_kwargs["disable_notification"])
        self.storage.add_ready_file.assert_awaited_once()
        persist_kwargs = self.storage.add_ready_file.await_args.kwargs
        self.assertEqual(persist_kwargs["revision_id"], self.revision_id)
        self.assertEqual(persist_kwargs["telegram_file_id"], "BQACAgIAAxkBAA-test")
        self.assertEqual(persist_kwargs["telegram_file_unique_id"], "AgAD-test")
        self.assertEqual(persist_kwargs["storage_message_id"], 555)
        self.assertEqual(persist_kwargs["max_files"], 3)
        self.assertFalse(path.exists())

    async def test_telegram_failure_leaves_no_ready_attachment_and_removes_temp(self):
        path = temp_file()
        self.bot.send_document.side_effect = RuntimeError("telegram down")

        with self.assertRaisesRegex(RuntimeError, "telegram down"):
            await self.service.attach_from_temp(
                author_user_id=77,
                submission_id=self.submission_id,
                temp_path=path,
                original_filename="story.txt",
                declared_mime="text/plain",
                now=100,
            )

        self.storage.add_ready_file.assert_not_awaited()
        self.assertFalse(path.exists())

    async def test_db_failure_after_telegram_success_cleans_staging_message(self):
        path = temp_file()
        self.storage.add_ready_file.side_effect = RuntimeError("database down")

        with self.assertRaisesRegex(RuntimeError, "database down"):
            await self.service.attach_from_temp(
                author_user_id=77,
                submission_id=self.submission_id,
                temp_path=path,
                original_filename="story.txt",
                declared_mime="text/plain",
                now=100,
            )

        self.bot.delete_message.assert_awaited_once_with(
            chat_id=-100222,
            message_id=555,
        )
        self.assertFalse(path.exists())

    async def test_delete_other_users_attachment_is_denied(self):
        self.storage.delete_ready_file.side_effect = NotFoundError("not found")

        with self.assertRaises(NotFoundError):
            await self.service.delete_attachment(
                author_user_id=88,
                submission_id=self.submission_id,
                file_id=self.persisted.id,
                now=100,
            )

    async def test_delete_sealed_revision_attachment_is_denied(self):
        self.storage.delete_ready_file.side_effect = ConflictError("sealed")

        with self.assertRaises(ConflictError):
            await self.service.delete_attachment(
                author_user_id=77,
                submission_id=self.submission_id,
                file_id=self.persisted.id,
                now=100,
            )


if __name__ == "__main__":
    unittest.main()

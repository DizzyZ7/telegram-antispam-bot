from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from writers_submission.delivery import WritersDeliveryWorker
from writers_submission.models import OutboxEventType, OutboxState


class TelegramForbiddenError(Exception):
    pass


def moderation_item(*, attempt_count: int = 1):
    return SimpleNamespace(
        id=uuid4(),
        submission_id=uuid4(),
        revision_id=uuid4(),
        event_type=OutboxEventType.MODERATION_CARD,
        state=OutboxState.IN_FLIGHT,
        attempt_count=attempt_count,
        worker_id="worker-test",
    )


def author_item(*, attempt_count: int = 1):
    return SimpleNamespace(
        id=uuid4(),
        submission_id=uuid4(),
        revision_id=uuid4(),
        event_type=OutboxEventType.AUTHOR_NOTIFICATION,
        state=OutboxState.IN_FLIGHT,
        attempt_count=attempt_count,
        worker_id="worker-test",
    )


class WritersDeliveryWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.storage = SimpleNamespace(
            claim_due_outbox=AsyncMock(return_value=[]),
            get_or_create_moderation_token=AsyncMock(return_value="opaque-token"),
            get_moderation_delivery_context=AsyncMock(
                return_value=SimpleNamespace(
                    author_user_id=77,
                    title="<b>Опасное & название</b>",
                    work_type="Рассказ",
                    genre="Фантастика",
                    description="<script>alert(1)</script>",
                    body_text="Текст произведения",
                    external_url="https://example.test/work?a=1&b=2",
                    files=(
                        SimpleNamespace(
                            telegram_file_id="telegram-file-1",
                            safe_filename="story.pdf",
                        ),
                    ),
                )
            ),
            get_author_notification_context=AsyncMock(
                return_value=SimpleNamespace(
                    author_user_id=77,
                    title="<b>Название</b>",
                    action="APPROVE",
                    comment=None,
                )
            ),
            mark_outbox_delivered=AsyncMock(),
            mark_outbox_retryable=AsyncMock(),
            mark_outbox_permanent_failure=AsyncMock(),
        )
        self.bot = SimpleNamespace(
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=10)),
            send_document=AsyncMock(return_value=SimpleNamespace(message_id=11)),
        )
        self.config = SimpleNamespace(moderation_chat_id=-100111)
        self.worker = WritersDeliveryWorker(
            self.bot,
            self.storage,
            self.config,
            poll_seconds=2,
            lease_seconds=60,
            batch_size=10,
            worker_id="worker-test",
        )

    async def test_moderation_card_escapes_text_and_sends_files_by_file_id(self):
        item = moderation_item()
        self.storage.claim_due_outbox.return_value = [item]

        processed = await self.worker.run_once(now=100)

        self.assertEqual(processed, 1)
        self.bot.send_document.assert_awaited_once()
        document_kwargs = self.bot.send_document.await_args.kwargs
        self.assertEqual(document_kwargs["chat_id"], -100111)
        self.assertEqual(document_kwargs["document"], "telegram-file-1")

        self.bot.send_message.assert_awaited_once()
        text = self.bot.send_message.await_args.kwargs["text"]
        self.assertIn("&lt;b&gt;Опасное &amp; название&lt;/b&gt;", text)
        self.assertNotIn("<script>", text)
        self.assertIn("https://example.test/work?a=1&amp;b=2", text)
        self.storage.mark_outbox_delivered.assert_awaited_once_with(
            outbox_id=item.id,
            worker_id="worker-test",
            now=100,
        )
        self.storage.mark_outbox_retryable.assert_not_awaited()
        self.storage.mark_outbox_permanent_failure.assert_not_awaited()

    async def test_moderation_card_contains_compact_opaque_controls(self):
        item = moderation_item()
        self.storage.claim_due_outbox.return_value = [item]

        await self.worker.run_once(now=150)

        self.storage.get_or_create_moderation_token.assert_awaited_once_with(
            submission_id=item.submission_id,
            revision_id=item.revision_id,
            now=150,
        )
        kwargs = self.bot.send_message.await_args.kwargs
        keyboard = kwargs["reply_markup"]
        callback_data = [
            button.callback_data
            for row in keyboard.inline_keyboard
            for button in row
        ]
        self.assertEqual(
            callback_data,
            [
                "ws:c:opaque-token",
                "ws:a:opaque-token",
                "ws:x:opaque-token",
                "ws:r:opaque-token",
            ],
        )
        self.assertTrue(all(len(value) <= 64 for value in callback_data))

    async def test_transient_failure_schedules_bounded_retry_without_false_success(self):
        item = moderation_item(attempt_count=2)
        self.storage.claim_due_outbox.return_value = [item]
        self.bot.send_document.side_effect = RuntimeError("temporary network failure")

        processed = await self.worker.run_once(now=200)

        self.assertEqual(processed, 1)
        self.storage.mark_outbox_delivered.assert_not_awaited()
        self.storage.mark_outbox_permanent_failure.assert_not_awaited()
        self.storage.mark_outbox_retryable.assert_awaited_once()
        retry_kwargs = self.storage.mark_outbox_retryable.await_args.kwargs
        self.assertEqual(retry_kwargs["outbox_id"], item.id)
        self.assertEqual(retry_kwargs["worker_id"], "worker-test")
        self.assertEqual(retry_kwargs["now"], 200)
        self.assertGreaterEqual(retry_kwargs["next_attempt_at"], 204)
        self.assertLessEqual(retry_kwargs["next_attempt_at"], 507)
        self.assertNotIn("temporary network failure", retry_kwargs["error_code"])

    async def test_forbidden_failure_is_permanent(self):
        item = moderation_item()
        self.storage.claim_due_outbox.return_value = [item]
        self.bot.send_document.side_effect = TelegramForbiddenError("blocked")

        await self.worker.run_once(now=300)

        self.storage.mark_outbox_permanent_failure.assert_awaited_once()
        permanent_kwargs = self.storage.mark_outbox_permanent_failure.await_args.kwargs
        self.assertEqual(permanent_kwargs["outbox_id"], item.id)
        self.assertEqual(permanent_kwargs["error_code"], "TelegramForbiddenError")
        self.storage.mark_outbox_retryable.assert_not_awaited()
        self.storage.mark_outbox_delivered.assert_not_awaited()

    async def test_author_notification_uses_private_chat_and_marks_delivered(self):
        item = author_item()
        self.storage.claim_due_outbox.return_value = [item]

        await self.worker.run_once(now=400)

        self.bot.send_message.assert_awaited_once()
        kwargs = self.bot.send_message.await_args.kwargs
        self.assertEqual(kwargs["chat_id"], 77)
        self.assertIn("одобр", kwargs["text"].casefold())
        self.assertNotIn("<b>Название</b>", kwargs["text"])
        self.storage.mark_outbox_delivered.assert_awaited_once_with(
            outbox_id=item.id,
            worker_id="worker-test",
            now=400,
        )


    async def test_background_loop_uses_restart_stable_epoch_clock(self):
        epoch_now = 1_800_000_000
        worker = WritersDeliveryWorker(
            self.bot,
            self.storage,
            self.config,
            poll_seconds=2,
            lease_seconds=60,
            batch_size=10,
            worker_id="epoch-worker",
            now_fn=lambda: epoch_now,
        )
        worker.run_once = AsyncMock(
            side_effect=lambda **kwargs: worker._stop_event.set()
        )

        await worker._run_forever()

        worker.run_once.assert_awaited_once_with(now=epoch_now)


if __name__ == "__main__":
    unittest.main()

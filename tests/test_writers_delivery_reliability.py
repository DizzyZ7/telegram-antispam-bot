from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from writers_submission.delivery import WritersDeliveryWorker
from writers_submission.models import OutboxEventType
from writers_submission.service import WritersSubmissionService


class ModerationDeliveryStateTests(unittest.IsolatedAsyncioTestCase):
    async def test_delivery_states_are_scoped_and_distinguish_delay_from_receipt(self):
        ids = [uuid4() for _ in range(5)]
        storage = SimpleNamespace(
            list_author_moderation_delivery=AsyncMock(return_value=[
                {"submission_id": str(ids[0]), "state": "PENDING",
                 "created_at": 950, "updated_at": 950},
                {"submission_id": str(ids[1]), "state": "RETRYABLE_FAILED",
                 "created_at": 100, "updated_at": 990},
                {"submission_id": str(ids[2]), "state": "DELIVERED",
                 "created_at": 50, "updated_at": 120},
                {"submission_id": str(ids[3]), "state": "PERMANENT_FAILED",
                 "created_at": 900, "updated_at": 915},
                {"submission_id": str(ids[4]), "state": None,
                 "created_at": 0, "updated_at": 0},
            ])
        )
        service = WritersSubmissionService(storage, SimpleNamespace(), AsyncMock(return_value=True))
        found = await service.moderation_delivery_statuses(77, now=1000)
        self.assertEqual(found[str(ids[0])]["status"], "QUEUED")
        self.assertEqual(found[str(ids[1])]["status"], "DELAYED")
        self.assertEqual(found[str(ids[2])]["status"], "DELIVERED")
        self.assertEqual(found[str(ids[3])]["status"], "NEEDS_ATTENTION")
        self.assertEqual(found[str(ids[4])]["status"], "NEEDS_ATTENTION")
        self.assertNotIn("error", str(found).lower())
        storage.list_author_moderation_delivery.assert_awaited_once_with(
            author_user_id=77, submission_id=None,
        )


class ModerationPacketResumptionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.item = SimpleNamespace(
            id=uuid4(), submission_id=uuid4(), revision_id=uuid4(),
            event_type=OutboxEventType.MODERATION_CARD,
            attempt_count=1, progress={}, payload={},
        )
        self.first_id = uuid4()
        self.second_id = uuid4()
        self.storage = SimpleNamespace(
            claim_due_outbox=AsyncMock(return_value=[self.item]),
            get_or_create_moderation_token=AsyncMock(return_value="opaque-token"),
            get_moderation_delivery_context=AsyncMock(return_value=SimpleNamespace(
                author_user_id=77, title="Work", work_type="Оридж",
                genre="Джен", description="Описание", body_text="",
                external_url="https://ficbook.net/readfic/1",
                details={"visual_mode": "image", "cover_file_id": str(self.first_id)},
                files=(
                    SimpleNamespace(id=self.first_id, telegram_file_id="tg-file-a",
                                    safe_filename="first.jpg", detected_file_class="jpeg"),
                    SimpleNamespace(id=self.second_id, telegram_file_id="tg-file-b",
                                    safe_filename="second.pdf", detected_file_class="pdf"),
                ),
            )),
            record_outbox_progress=AsyncMock(),
            mark_outbox_delivered=AsyncMock(),
            mark_outbox_retryable=AsyncMock(),
            mark_outbox_permanent_failure=AsyncMock(),
        )
        self.bot = SimpleNamespace(
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=10)),
            send_document=AsyncMock(),
            send_photo=AsyncMock(),
        )
        self.worker = WritersDeliveryWorker(
            self.bot, self.storage,
            SimpleNamespace(moderation_chat_id=-100321, owner_user_id=None),
            worker_id="moderation-test", lease_seconds=60,
        )

    async def test_partial_packet_resumes_without_duplicate_card_or_saved_attachment(self):
        self.bot.send_document.side_effect = [
            SimpleNamespace(message_id=11),
            RuntimeError("telegram disconnected"),
        ]
        await self.worker.run_once(now=100)

        self.storage.mark_outbox_retryable.assert_awaited_once()
        calls = self.storage.record_outbox_progress.await_args_list
        self.assertEqual([call.kwargs["key"] for call in calls], [
            "card", "file:" + str(self.first_id),
        ])
        self.assertEqual(
            [call.kwargs["message_id"] for call in calls], [10, 11]
        )
        self.storage.mark_outbox_delivered.assert_not_awaited()
        self.assertEqual(self.bot.send_message.await_count, 1)

        resumed = SimpleNamespace(
            **{**vars(self.item),
               "attempt_count": 2,
               "progress": {"card": 10, "file:" + str(self.first_id): 11}}
        )
        self.storage.claim_due_outbox.return_value = [resumed]
        self.bot.send_document.side_effect = [
            SimpleNamespace(message_id=12),
        ]
        await self.worker.run_once(now=110)

        self.assertEqual(self.bot.send_message.await_count, 1)
        self.storage.mark_outbox_delivered.assert_awaited_once_with(
            outbox_id=self.item.id, worker_id="moderation-test", now=110,
            delivery_chat_id=-100321, delivery_message_ids=(10, 11, 12),
        )
        self.assertEqual(
            self.storage.record_outbox_progress.await_args.kwargs["key"],
            "file:" + str(self.second_id),
        )

    async def test_checkpoint_failure_halts_packet_before_sending_files(self):
        self.storage.record_outbox_progress.side_effect = RuntimeError("database down")
        await self.worker.run_once(now=100)
        self.bot.send_message.assert_awaited_once()
        self.bot.send_document.assert_not_awaited()
        self.storage.mark_outbox_delivered.assert_not_awaited()
        self.storage.mark_outbox_retryable.assert_awaited_once()

    async def test_watchdog_sends_rate_limited_owner_alerts_without_author_data(self):
        self.storage.claim_due_outbox.return_value = []
        self.storage.get_moderation_delivery_health = AsyncMock(return_value={
            "problem_count": 4,
            "by_state": {"PERMANENT_FAILED": 1, "RETRYABLE_FAILED": 3},
            "oldest_at": 10,
        })
        self.worker.config.owner_user_id = 999
        await self.worker.run_once(now=1000)
        self.bot.send_message.assert_awaited_once()
        message = self.bot.send_message.await_args.kwargs
        self.assertEqual(message["chat_id"], 999)
        self.assertIn("4", message["text"])
        self.assertNotIn("77", message["text"])
        await self.worker.run_once(now=1065)
        self.assertEqual(self.bot.send_message.await_count, 1)
        await self.worker.run_once(now=2000)
        self.assertEqual(self.bot.send_message.await_count, 1)


if __name__ == "__main__":
    unittest.main()

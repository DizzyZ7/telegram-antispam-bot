from __future__ import annotations

import unittest
from io import BytesIO
from PIL import Image
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
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
        payload={},
    )


def author_item(*, attempt_count: int = 1, kind: str = "APPROVED"):
    return SimpleNamespace(
        id=uuid4(),
        submission_id=uuid4(),
        revision_id=uuid4(),
        event_type=OutboxEventType.AUTHOR_NOTIFICATION,
        state=OutboxState.IN_FLIGHT,
        attempt_count=attempt_count,
        worker_id="worker-test",
        payload={"kind": kind},
    )


class WritersDeliveryWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.storage = SimpleNamespace(
            claim_due_outbox=AsyncMock(return_value=[]),
            get_or_create_moderation_token=AsyncMock(return_value="opaque-token"),
            is_cover_payment_confirmed=AsyncMock(return_value=True),
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
                side_effect=lambda **kwargs: SimpleNamespace(
                    author_user_id=77,
                    title="<b>Название</b>",
                    action=kwargs.get("notification_kind") or "APPROVED",
                    comment=(
                        "Нужно поправить"
                        if kwargs.get("notification_kind") == "CHANGES_REQUESTED"
                        else None
                    ),
                )
            ),
            mark_outbox_delivered=AsyncMock(),
            mark_outbox_retryable=AsyncMock(),
            mark_outbox_permanent_failure=AsyncMock(),
        )
        self.bot = SimpleNamespace(
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=10)),
            send_document=AsyncMock(return_value=SimpleNamespace(message_id=11)),
            send_photo=AsyncMock(return_value=SimpleNamespace(message_id=12)),
            download=AsyncMock(),
        )
        self.config = SimpleNamespace(moderation_chat_id=-100111, owner_user_id=2039781854)
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
            delivery_chat_id=-100111,
            delivery_message_ids=(11, 10),
        )
        self.storage.mark_outbox_retryable.assert_not_awaited()
        self.storage.mark_outbox_permanent_failure.assert_not_awaited()

    async def test_moderation_card_and_attachment_go_to_owner_dm_not_group(self):
        # Delivery consumes the effective destination from validated config.
        # A stale group value in the hosting ENV must not be consulted here.
        self.config.moderation_chat_id = 2039781854
        item = moderation_item()
        self.storage.claim_due_outbox.return_value = [item]

        await self.worker.run_once(now=145)

        self.bot.send_document.assert_awaited_once()
        self.assertEqual(
            self.bot.send_document.await_args.kwargs["chat_id"], 2039781854
        )
        self.bot.send_message.assert_awaited_once()
        card = self.bot.send_message.await_args.kwargs
        self.assertEqual(card["chat_id"], 2039781854)
        self.assertIn("Новая работа на модерацию", card["text"])
        self.assertTrue(card["reply_markup"].inline_keyboard)
        self.storage.mark_outbox_delivered.assert_awaited_once_with(
            outbox_id=item.id, worker_id="worker-test", now=145,
            delivery_chat_id=2039781854, delivery_message_ids=(11, 10),
        )

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

    async def test_maximum_size_moderation_fields_fit_telegram_limit(self):
        item = moderation_item()
        context = self.storage.get_moderation_delivery_context.return_value
        context.title = "<&😀" * 1000
        context.work_type = "&" * 500
        context.genre = "<" * 500
        context.description = "<script>&😀" * 1000
        context.external_url = "https://example.test/?q=" + "&key=value" * 500
        context.body_text = "<b>😀&" * 50_000
        original_body = context.body_text
        self.storage.claim_due_outbox.return_value = [item]

        await self.worker.run_once(now=175)

        sent = self.bot.send_message.await_args.kwargs["text"]
        self.assertLessEqual(len(sent.encode("utf-16-le")) // 2, 3900)
        self.assertIn("…", sent)
        self.assertNotIn("<script>", sent)
        self.assertEqual(context.body_text, original_body)
        self.storage.mark_outbox_delivered.assert_awaited_once()
        self.storage.mark_outbox_permanent_failure.assert_not_awaited()

    async def test_long_author_decision_comment_fits_telegram_limit(self):
        item = author_item(kind="CHANGES_REQUESTED")
        self.storage.claim_due_outbox.return_value = [item]
        self.storage.get_author_notification_context.side_effect = None
        self.storage.get_author_notification_context.return_value = SimpleNamespace(
            author_user_id=77,
            title="<&😀" * 1000,
            action="CHANGES_REQUESTED",
            comment="<script>&😀" * 1000,
        )

        await self.worker.run_once(now=180)

        sent = self.bot.send_message.await_args.kwargs["text"]
        self.assertLessEqual(len(sent.encode("utf-16-le")) // 2, 3900)
        self.assertIn("…", sent)
        self.assertNotIn("<script>", sent)
        self.storage.mark_outbox_delivered.assert_awaited_once()
        self.storage.mark_outbox_permanent_failure.assert_not_awaited()

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
        self.storage.get_author_notification_context.assert_awaited_once_with(
            submission_id=item.submission_id,
            revision_id=item.revision_id,
            notification_kind="APPROVED",
        )
        self.storage.mark_outbox_delivered.assert_awaited_once_with(
            outbox_id=item.id,
            worker_id="worker-test",
            now=400,
            delivery_chat_id=77,
            delivery_message_ids=(10,),
        )


    async def test_submission_accepted_and_withdrawn_notifications_are_rendered(self):
        cases = (
            ("SUBMISSION_ACCEPTED", "принят"),
            ("WITHDRAWN", "отозван"),
        )
        for kind, expected in cases:
            with self.subTest(kind=kind):
                self.bot.send_message.reset_mock()
                self.storage.get_author_notification_context.reset_mock()
                self.storage.mark_outbox_delivered.reset_mock()
                item = author_item(kind=kind)
                self.storage.claim_due_outbox.return_value = [item]

                await self.worker.run_once(now=450)

                kwargs = self.bot.send_message.await_args.kwargs
                self.assertEqual(kwargs["chat_id"], 77)
                self.assertIn(expected, kwargs["text"].casefold())
                self.storage.get_author_notification_context.assert_awaited_once_with(
                    submission_id=item.submission_id,
                    revision_id=item.revision_id,
                    notification_kind=kind,
                )


    async def test_approved_preview_with_palette_goes_only_to_owner_dm_as_photo(self):
        item = moderation_item()
        item.event_type = OutboxEventType.OWNER_PREVIEW
        self.storage.claim_due_outbox.return_value = [item]
        context = self.storage.get_moderation_delivery_context.return_value
        context.title = "Однажды в Лост-Крике"
        context.work_type = "Оридж"
        context.genre = "Джен"
        context.description = "Описание истории <без HTML>"
        context.external_url = "https://ficbook.net/readfic/example"
        context.details = {
            "form_version": 2,
            "size_category": "макси",
            "rating": "R",
            "completion": "в процессе",
            "palette_colors": ["#FF0000", "#00FF00", "#0000FF", "#111111"],
            "visual_mode": "palette",
            "characters": "Джонатан Кросс/Лиам Миллер",
            "notes": "",
            "extra_links": ["https://t.me/ikf_channel"],
        }

        result = await self.worker.run_once(now=500)

        self.assertEqual(result, 1)
        self.bot.send_photo.assert_awaited_once()
        kwargs = self.bot.send_photo.await_args.kwargs
        self.assertEqual(kwargs["chat_id"], 2039781854)
        self.assertEqual(kwargs["parse_mode"], "HTML")
        self.assertTrue(kwargs["photo"].data.startswith(bytes.fromhex("89504e470d0a1a0a")))
        self.assertIn("#Ориджиналы | #Макси | #Джен | #R | #ВПроцессе", kwargs["caption"])
        self.assertIn("Основные персонажи", kwargs["caption"])
        self.assertIn("&lt;без HTML&gt;", kwargs["caption"])
        self.assertIn("Читать на Фикбуке", kwargs["caption"])
        self.assertIn("ТГ-канал", kwargs["caption"])
        self.bot.send_message.assert_not_awaited()
        self.storage.mark_outbox_delivered.assert_awaited_once_with(
            outbox_id=item.id, worker_id="worker-test", now=500,
            delivery_chat_id=2039781854, delivery_message_ids=(12,),
        )

    async def test_long_owner_post_sends_image_then_complete_text_to_owner_only(self):
        item = moderation_item()
        item.event_type = OutboxEventType.OWNER_PREVIEW
        self.storage.claim_due_outbox.return_value = [item]
        context = self.storage.get_moderation_delivery_context.return_value
        context.work_type = "ФФ"
        context.description = "Долгое описание. " * 100
        context.external_url = "https://ficbook.net/readfic/hello"
        context.details = {
            "form_version": 2, "fandom": "Атака Титанов",
            "size_category": "макси", "rating": "NC-17",
            "completion": "в процессе", "visual_mode": "image",
            "extra_links": [], "notes": "", "characters": "Эрвин/Леви",
        }
        context.files = (SimpleNamespace(
            telegram_file_id="file-cover", safe_filename="cover.png",
            detected_file_class="png",
        ),)
        async def download(*args, **kwargs):
            self.assertEqual(args[0], "file-cover")
            with BytesIO() as temp:
                Image.new("RGB", (400, 260), (30, 50, 90)).save(temp, "PNG")
                kwargs["destination"].write(temp.getvalue())
        self.bot.download.side_effect = download

        await self.worker.run_once(now=510)

        self.bot.send_photo.assert_awaited_once()
        self.bot.send_message.assert_awaited_once()
        self.assertEqual(self.bot.send_photo.await_args.kwargs["chat_id"], 2039781854)
        self.assertEqual(self.bot.send_message.await_args.kwargs["chat_id"], 2039781854)
        self.assertIn("#АтакаТитанов", self.bot.send_message.await_args.kwargs["text"])
        self.assertIn("#NC17", self.bot.send_message.await_args.kwargs["text"])
        self.assertIn("Долгое описание.", self.bot.send_message.await_args.kwargs["text"])
        self.storage.mark_outbox_delivered.assert_awaited_once_with(
            outbox_id=item.id, worker_id="worker-test", now=510,
            delivery_chat_id=2039781854, delivery_message_ids=(12, 10),
        )

    async def test_unpaid_custom_cover_never_reaches_owner_as_photo(self):
        item = moderation_item()
        item.event_type = OutboxEventType.OWNER_PREVIEW
        self.storage.claim_due_outbox.return_value = [item]
        self.storage.is_cover_payment_confirmed.return_value = False
        context = self.storage.get_moderation_delivery_context.return_value
        context.details = {"visual_mode": "image", "form_version": 2,
                           "extra_links": [], "size_category": "макси",
                           "rating": "R", "completion": "в процессе"}
        await self.worker.run_once(now=511)
        self.bot.download.assert_not_awaited()
        self.bot.send_photo.assert_not_awaited()
        self.bot.send_document.assert_not_awaited()
        message = self.bot.send_message.await_args.kwargs
        self.assertEqual(message["chat_id"], 2039781854)
        self.assertIn("ожидает подтверждения оплаты", message["text"])

    async def test_failed_palette_render_falls_back_to_owner_text(self):
        item = moderation_item()
        item.event_type = OutboxEventType.OWNER_PREVIEW
        self.storage.claim_due_outbox.return_value = [item]
        context = self.storage.get_moderation_delivery_context.return_value
        context.title = "Тест"
        context.details = {
            "form_version": 2, "visual_mode": "palette",
            "palette_colors": ["#123456", "#234567", "#345678", "#456789"],
            "size_category": "мини", "rating": "G",
            "completion": "в процессе", "extra_links": [],
        }
        with patch("writers_submission.delivery.render_palette_png",
                   side_effect=RuntimeError("test image renderer broken")):
            with self.assertLogs("writers_submission.delivery", level="ERROR") as log:
                await self.worker.run_once(now=518)
        self.assertIn("WRITERS_OWNER_PREVIEW_IMAGE_PREP_FAILED", "\n".join(log.output))
        self.bot.send_photo.assert_not_awaited()
        self.bot.send_message.assert_awaited_once()
        kwargs = self.bot.send_message.await_args.kwargs
        self.assertEqual(kwargs["chat_id"], 2039781854)
        self.assertIn("Тест", kwargs["text"])
        self.assertIn("Не удалось подготовить макет", kwargs["text"])
        self.storage.mark_outbox_delivered.assert_awaited_once()

    async def test_failed_photo_send_falls_back_to_owner_text(self):
        item = moderation_item()
        item.event_type = OutboxEventType.OWNER_PREVIEW
        self.storage.claim_due_outbox.return_value = [item]
        context = self.storage.get_moderation_delivery_context.return_value
        context.title = "Тест"
        context.details = {
            "form_version": 2, "visual_mode": "palette",
            "palette_colors": ["#123456", "#234567", "#345678", "#456789"],
            "size_category": "мини", "rating": "G",
            "completion": "в процессе", "extra_links": [],
        }
        self.bot.send_photo.side_effect = RuntimeError("Telegram rejected photo")
        with self.assertLogs("writers_submission.delivery", level="ERROR") as log:
            await self.worker.run_once(now=519)
        self.assertIn("WRITERS_OWNER_PREVIEW_IMAGE_SEND_FAILED", "\n".join(log.output))
        self.bot.send_message.assert_awaited_once()
        self.assertEqual(self.bot.send_message.await_args.kwargs["chat_id"], 2039781854)
        self.assertIn("Тест", self.bot.send_message.await_args.kwargs["text"])
        self.storage.mark_outbox_delivered.assert_awaited_once()

    async def test_forbidden_owner_dm_stays_failed_with_visible_diagnostics(self):
        item = moderation_item()
        item.event_type = OutboxEventType.OWNER_PREVIEW
        self.storage.claim_due_outbox.return_value = [item]
        context = self.storage.get_moderation_delivery_context.return_value
        context.title = "Тест"
        context.details = {"visual_mode": None, "extra_links": []}
        self.bot.send_message.side_effect = TelegramForbiddenError("bot blocked by user")
        with self.assertLogs("writers_submission.delivery", level="ERROR") as logs:
            await self.worker.run_once(now=520)
        self.assertIn("WRITERS_DELIVERY_FAILED event=OWNER_PREVIEW", "\n".join(logs.output))
        self.storage.mark_outbox_permanent_failure.assert_awaited_once()
        self.storage.mark_outbox_delivered.assert_not_awaited()

    async def test_non_owner_events_never_trigger_owner_preview(self):
        item = author_item(kind="APPROVED")
        self.storage.claim_due_outbox.return_value = [item]
        await self.worker.run_once(now=515)
        self.assertEqual(self.bot.send_message.await_args.kwargs["chat_id"], 77)
        self.bot.send_photo.assert_not_awaited()
        self.storage.get_moderation_delivery_context.assert_not_awaited()

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

from __future__ import annotations

import html
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from aiogram import Dispatcher, F

from writers_submission.models import ReviewAction, SubmissionStatus
from writers_submission.handlers import (
    PendingModerationCommentFilter,
    WritersSubmissionStartFilter,
    register_writers_submission_handlers,
)


MOD_CHAT_ID = -100111
MODERATOR_ID = 9001


def make_app():
    return SimpleNamespace(
        dp=Dispatcher(),
        bot=SimpleNamespace(
            send_message=AsyncMock(
                return_value=SimpleNamespace(message_id=777),
            ),
        ),
    )


def make_service():
    submission_id = uuid4()
    revision_id = uuid4()
    target = SimpleNamespace(
        submission_id=submission_id,
        revision_id=revision_id,
    )
    result_submission = SimpleNamespace(
        id=submission_id,
        status=SubmissionStatus.IN_REVIEW,
        claimed_by_user_id=MODERATOR_ID,
    )
    return SimpleNamespace(
        target=target,
        resolve_moderation_token=AsyncMock(return_value=target),
        claim=AsyncMock(
            return_value=SimpleNamespace(
                submission=result_submission,
                action=ReviewAction.CLAIM,
                reviewer_user_id=MODERATOR_ID,
                applied=True,
                comment=None,
            )
        ),
        decide=AsyncMock(
            return_value=SimpleNamespace(
                submission=SimpleNamespace(
                    id=submission_id,
                    status=SubmissionStatus.APPROVED,
                    claimed_by_user_id=MODERATOR_ID,
                ),
                action=ReviewAction.APPROVE,
                reviewer_user_id=MODERATOR_ID,
                applied=True,
                comment=None,
            )
        ),
    )


def make_config():
    return SimpleNamespace(
        public_url="https://example.test/writers/",
        moderation_chat_id=MOD_CHAT_ID,
        moderator_ids=frozenset({MODERATOR_ID}),
    )


def user(user_id=MODERATOR_ID):
    return SimpleNamespace(id=user_id, username="moderator", full_name="Moderator")


def start_message(*, private=True):
    answer = AsyncMock()
    return SimpleNamespace(
        chat=SimpleNamespace(
            id=77 if private else -1002619489118,
            type="private" if private else "supergroup",
        ),
        from_user=user(77),
        text="/start writers_submit",
        answer=answer,
    )


def moderation_callback(
    data: str,
    *,
    actor_id=MODERATOR_ID,
    edit_side_effect=None,
):
    edit_reply_markup = AsyncMock()
    if edit_side_effect is not None:
        edit_reply_markup.side_effect = edit_side_effect
    return SimpleNamespace(
        data=data,
        from_user=user(actor_id),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=MOD_CHAT_ID, type="supergroup"),
            message_id=555,
            edit_reply_markup=edit_reply_markup,
        ),
        answer=AsyncMock(),
    )


def comment_message(
    text: str,
    *,
    actor_id=MODERATOR_ID,
    reply_to_message_id: int | None = 777,
):
    return SimpleNamespace(
        chat=SimpleNamespace(id=MOD_CHAT_ID, type="supergroup"),
        from_user=user(actor_id),
        text=text,
        reply_to_message=(
            SimpleNamespace(message_id=reply_to_message_id)
            if reply_to_message_id is not None
            else None
        ),
        answer=AsyncMock(),
    )


def handlers_by_name(app, observer_name):
    return {
        item.callback.__name__: item.callback
        for item in getattr(app.dp, observer_name).handlers
    }


class WritersOwnerDeliveryRecoveryHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.owner_id = 2039781854
        self.app = make_app()
        self.storage = SimpleNamespace(
            get_owner_preview_delivery_status=AsyncMock(return_value=[
                {"submission_id": uuid4(), "revision_id": uuid4(),
                 "state": "PERMANENT_FAILED", "attempt_count": 1,
                 "last_error_code": "TelegramForbiddenError", "title": "Тест"}
            ]),
            retry_failed_owner_preview=AsyncMock(return_value=uuid4()),
        )
        self.service = make_service()
        self.service.storage = self.storage
        config = SimpleNamespace(
            public_url="https://example.test/writers/",
            moderation_chat_id=self.owner_id,
            moderator_ids=frozenset({self.owner_id}),
            owner_user_id=self.owner_id,
        )
        register_writers_submission_handlers(self.app, self.service, config)
        handlers = handlers_by_name(self.app, "message")
        self.status = handlers["writers_delivery_status"]
        self.retry = handlers["writers_retry_preview"]

    def private_message(self, text, *, actor_id=None, private=True):
        actor_id = self.owner_id if actor_id is None else actor_id
        return SimpleNamespace(
            chat=SimpleNamespace(
                id=actor_id if private else -100111,
                type="private" if private else "supergroup",
            ),
            from_user=user(actor_id),
            text=text,
            answer=AsyncMock(),
        )

    async def test_owner_can_inspect_failed_preview(self):
        message = self.private_message("/writers_delivery")
        await self.status(message)
        self.storage.get_owner_preview_delivery_status.assert_awaited_once()
        body = message.answer.await_args.args[0]
        self.assertIn("Тест", body)
        self.assertIn("PERMANENT_FAILED", body)
        self.assertIn("TelegramForbiddenError", body)

    async def test_only_owner_in_private_chat_can_requeue(self):
        for msg in (
            self.private_message("/writers_retry", actor_id=12345),
            self.private_message("/writers_retry", private=False),
        ):
            await self.retry(msg)
            msg.answer.assert_not_awaited()
        self.storage.retry_failed_owner_preview.assert_not_awaited()

        target = uuid4()
        owner = self.private_message(f"/writers_retry {target}")
        await self.retry(owner)
        self.storage.retry_failed_owner_preview.assert_awaited_once()
        self.assertEqual(
            self.storage.retry_failed_owner_preview.await_args.kwargs["submission_id"],
            target,
        )
        self.assertIn("поставлен в очередь", owner.answer.await_args.args[0])

    async def test_retry_rejects_invalid_uuid(self):
        owner = self.private_message("/writers_retry not-a-uuid")
        await self.retry(owner)
        self.storage.retry_failed_owner_preview.assert_not_awaited()
        self.assertIn("UUID", owner.answer.await_args.args[0])


class WritersSubmissionStartFilterTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_filter_matches_only_private_writers_submit(self):
        filter_ = WritersSubmissionStartFilter()

        private_target = start_message(private=True)
        self.assertTrue(await filter_(private_target))

        plain_start = start_message(private=True)
        plain_start.text = "/start"
        self.assertFalse(await filter_(plain_start))

        other_start = start_message(private=True)
        other_start.text = "/start something_else"
        self.assertFalse(await filter_(other_start))

        group_target = start_message(private=False)
        self.assertFalse(await filter_(group_target))


class WritersSubmissionHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = [100.0]
        self.app = make_app()
        self.service = make_service()
        self.config = make_config()
        self.pending_comments = register_writers_submission_handlers(
            self.app,
            self.service,
            self.config,
            now_fn=lambda: self.now[0],
        )
        messages = handlers_by_name(self.app, "message")
        callbacks = handlers_by_name(self.app, "callback_query")
        self.start_handler = messages["writers_submission_start"]
        self.comment_handler = messages["writers_moderation_comment"]
        self.callback_handler = callbacks["writers_moderation_callback"]

    async def test_group_start_deep_link_does_not_grant_or_launch_webapp(self):
        message = start_message(private=False)

        await self.start_handler(message)

        message.answer.assert_not_awaited()

    async def test_private_start_sends_exact_webapp_url(self):
        message = start_message(private=True)

        await self.start_handler(message)

        message.answer.assert_awaited_once()
        kwargs = message.answer.await_args.kwargs
        keyboard = kwargs["reply_markup"]
        button = keyboard.inline_keyboard[0][0]
        self.assertEqual(button.text, "✒️ Отправить работу")
        self.assertEqual(button.web_app.url, self.config.public_url)

    async def test_non_moderator_callback_is_denied_before_token_resolution(self):
        callback = moderation_callback("ws:c:opaque-token", actor_id=9999)

        await self.callback_handler(callback)

        self.service.resolve_moderation_token.assert_not_awaited()
        self.service.claim.assert_not_awaited()
        callback.answer.assert_awaited_once()
        self.assertTrue(callback.answer.await_args.kwargs["show_alert"])

    async def test_owner_private_chat_allows_claim_and_decision_buttons(self):
        owner_id = 2039781854
        owner_app = make_app()
        owner_service = make_service()
        owner_config = SimpleNamespace(
            public_url="https://example.test/writers/",
            moderation_chat_id=owner_id,
            moderator_ids=frozenset({owner_id}),
        )
        register_writers_submission_handlers(owner_app, owner_service, owner_config)
        owner_callback = handlers_by_name(owner_app, "callback_query")[
            "writers_moderation_callback"
        ]

        take = moderation_callback("ws:c:opaque-token", actor_id=owner_id)
        take.message.chat.id = owner_id
        take.message.chat.type = "private"
        await owner_callback(take)
        owner_service.claim.assert_awaited_once()
        self.assertEqual(
            owner_service.claim.await_args.kwargs["reviewer_user_id"], owner_id
        )

        decision = moderation_callback("ws:a:opaque-token", actor_id=owner_id)
        decision.message.chat.id = owner_id
        decision.message.chat.type = "private"
        await owner_callback(decision)
        self.assertEqual(
            owner_service.decide.await_args.kwargs["reviewer_user_id"], owner_id
        )
        self.assertEqual(
            owner_service.decide.await_args.kwargs["action"], ReviewAction.APPROVE
        )

    async def test_owner_private_mode_rejects_stale_group_buttons_and_other_users(self):
        owner_id = 2039781854
        owner_app = make_app()
        owner_service = make_service()
        owner_config = SimpleNamespace(
            public_url="https://example.test/writers/",
            moderation_chat_id=owner_id,
            moderator_ids=frozenset({owner_id}),
        )
        register_writers_submission_handlers(owner_app, owner_service, owner_config)
        owner_callback = handlers_by_name(owner_app, "callback_query")[
            "writers_moderation_callback"
        ]

        from_group = moderation_callback("ws:a:opaque-token", actor_id=owner_id)
        await owner_callback(from_group)
        owner_service.resolve_moderation_token.assert_not_awaited()
        self.assertTrue(from_group.answer.await_args.kwargs["show_alert"])

        wrong_user = moderation_callback("ws:a:opaque-token", actor_id=12345)
        wrong_user.message.chat.id = owner_id
        wrong_user.message.chat.type = "private"
        await owner_callback(wrong_user)
        owner_service.resolve_moderation_token.assert_not_awaited()
        self.assertTrue(wrong_user.answer.await_args.kwargs["show_alert"])

    async def test_owner_private_comment_must_reply_to_exact_prompt(self):
        owner_id = 2039781854
        owner_app = make_app()
        owner_service = make_service()
        owner_config = SimpleNamespace(
            public_url="https://example.test/writers/",
            moderation_chat_id=owner_id,
            moderator_ids=frozenset({owner_id}),
        )
        pending = register_writers_submission_handlers(owner_app, owner_service, owner_config)
        cb = handlers_by_name(owner_app, "callback_query")[
            "writers_moderation_callback"
        ]
        comment = handlers_by_name(owner_app, "message")[
            "writers_moderation_comment"
        ]
        change = moderation_callback("ws:x:opaque-token", actor_id=owner_id)
        change.message.chat.id = owner_id
        change.message.chat.type = "private"
        await cb(change)
        self.assertIsNotNone(pending.current_for_user(owner_id))
        self.assertEqual(owner_app.bot.send_message.await_args.kwargs["chat_id"], owner_id)
        filter_ = PendingModerationCommentFilter(pending, owner_id)
        unrelated = comment_message("Не комментарий", actor_id=owner_id,
                                    reply_to_message_id=None)
        unrelated.chat.id = owner_id
        unrelated.chat.type = "private"
        self.assertFalse(await filter_(unrelated))
        await comment(unrelated)
        owner_service.decide.assert_not_awaited()

        reply = comment_message("Поправить описание", actor_id=owner_id)
        reply.chat.id = owner_id
        reply.chat.type = "private"
        self.assertTrue(await filter_(reply))
        await comment(reply)
        owner_service.decide.assert_awaited_once()
        self.assertEqual(owner_service.decide.await_args.kwargs["comment"],
                         "Поправить описание")

    async def test_claim_uses_callback_actor_not_payload_identity(self):
        callback = moderation_callback("ws:c:opaque-token")

        await self.callback_handler(callback)

        self.service.resolve_moderation_token.assert_awaited_once_with("opaque-token")
        self.service.claim.assert_awaited_once_with(
            reviewer_user_id=MODERATOR_ID,
            submission_id=self.service.target.submission_id,
            revision_id=self.service.target.revision_id,
            now=100,
        )
        callback.answer.assert_awaited_once()

    async def test_duplicate_decision_is_safe(self):
        self.service.decide.return_value = SimpleNamespace(
            submission=SimpleNamespace(
                id=self.service.target.submission_id,
                status=SubmissionStatus.APPROVED,
                claimed_by_user_id=MODERATOR_ID,
            ),
            action=ReviewAction.APPROVE,
            reviewer_user_id=MODERATOR_ID,
            applied=False,
            comment=None,
        )
        callback = moderation_callback("ws:a:opaque-token")

        await self.callback_handler(callback)

        self.service.decide.assert_awaited_once()
        callback.answer.assert_awaited_once()
        self.assertIn("уже", callback.answer.await_args.args[0].casefold())

    async def test_request_changes_waits_for_comment_then_decides(self):
        callback = moderation_callback("ws:x:opaque-token")

        await self.callback_handler(callback)

        self.service.decide.assert_not_awaited()

        message = comment_message("  Нужно усилить финал  ")
        await self.comment_handler(message)

        self.service.decide.assert_awaited_once_with(
            reviewer_user_id=MODERATOR_ID,
            submission_id=self.service.target.submission_id,
            revision_id=self.service.target.revision_id,
            action=ReviewAction.REQUEST_CHANGES,
            comment="Нужно усилить финал",
            now=100,
        )

    async def test_unrelated_moderator_text_is_not_a_review_comment(self):
        await self.callback_handler(moderation_callback("ws:x:opaque-token"))
        filter_ = PendingModerationCommentFilter(
            self.pending_comments, MOD_CHAT_ID
        )
        for reply_id in (None, 555, 776):
            with self.subTest(reply_id=reply_id):
                unrelated = comment_message(
                    "Обычное обсуждение в чате",
                    reply_to_message_id=reply_id,
                )
                self.assertFalse(await filter_(unrelated))
                await self.comment_handler(unrelated)
                self.service.decide.assert_not_awaited()
                unrelated.answer.assert_not_awaited()

        actual_reply = comment_message("Доработать финал")
        self.assertTrue(await filter_(actual_reply))
        await self.comment_handler(actual_reply)
        self.service.decide.assert_awaited_once()

    async def test_failed_prompt_does_not_arm_comment_capture(self):
        self.app.bot.send_message.side_effect = RuntimeError(
            "Telegram is unavailable"
        )
        callback = moderation_callback("ws:x:opaque-token")

        await self.callback_handler(callback)

        self.assertIsNone(
            self.pending_comments.current_for_user(MODERATOR_ID)
        )
        self.service.decide.assert_not_awaited()
        self.assertTrue(callback.answer.await_args.kwargs["show_alert"])

    async def test_new_prompt_invalidates_old_reply_anchor(self):
        self.app.bot.send_message.side_effect = [
            SimpleNamespace(message_id=777),
            SimpleNamespace(message_id=888),
        ]
        await self.callback_handler(moderation_callback("ws:x:opaque-token"))
        await self.callback_handler(moderation_callback("ws:x:opaque-token"))

        old_reply = comment_message(
            "Старый комментарий",
            reply_to_message_id=777,
        )
        await self.comment_handler(old_reply)
        self.service.decide.assert_not_awaited()

        await self.comment_handler(
            comment_message("Новый комментарий", reply_to_message_id=888)
        )
        self.service.decide.assert_awaited_once()

    async def test_expired_comment_state_is_rejected(self):
        callback = moderation_callback("ws:x:opaque-token")
        await self.callback_handler(callback)
        self.now[0] = 701.0

        message = comment_message("Поздний комментарий")
        await self.comment_handler(message)

        self.service.decide.assert_not_awaited()
        message.answer.assert_awaited_once()
        self.assertIn("истек", message.answer.await_args.args[0].casefold())

    async def test_telegram_card_edit_failure_does_not_undo_decision(self):
        callback = moderation_callback(
            "ws:a:opaque-token",
            edit_side_effect=RuntimeError("message is gone"),
        )

        await self.callback_handler(callback)

        self.service.decide.assert_awaited_once()
        callback.answer.assert_awaited_once()
        self.assertFalse(callback.answer.await_args.kwargs.get("show_alert", False))

    async def test_free_form_comment_is_escaped_when_echoed_to_telegram(self):
        callback = moderation_callback("ws:x:opaque-token")
        await self.callback_handler(callback)

        raw = "<b>сырой & текст</b>"
        message = comment_message(raw)
        await self.comment_handler(message)

        self.service.decide.assert_awaited_once()
        message.answer.assert_awaited_once()
        echoed = message.answer.await_args.args[0]
        self.assertIn(html.escape(raw), echoed)
        self.assertNotIn(raw, echoed)



    def test_default_moderation_clock_is_epoch_time(self):
        defaults = register_writers_submission_handlers.__kwdefaults__
        self.assertIs(defaults["now_fn"], time.time)

    async def test_submission_handlers_are_promoted_ahead_of_legacy_catchalls(self):
        app = make_app()

        @app.dp.message(F.text)
        async def legacy_text_catchall(message):
            return None

        @app.dp.callback_query(F.data)
        async def legacy_callback_catchall(callback):
            return None

        register_writers_submission_handlers(
            app,
            make_service(),
            make_config(),
            now_fn=lambda: 100.0,
        )

        message_names = [
            item.callback.__name__
            for item in app.dp.message.handlers[:4]
        ]
        callback_names = [
            item.callback.__name__
            for item in app.dp.callback_query.handlers[:1]
        ]
        self.assertEqual(
            set(message_names),
            {"writers_submission_start", "writers_moderation_comment",
             "writers_delivery_status", "writers_retry_preview"},
        )
        self.assertEqual(
            callback_names,
            ["writers_moderation_callback"],
        )


if __name__ == "__main__":
    unittest.main()

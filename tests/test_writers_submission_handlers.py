from __future__ import annotations

import html
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from aiogram import Dispatcher, F

from writers_submission.models import ReviewAction, SubmissionStatus
from writers_submission.handlers import register_writers_submission_handlers


MOD_CHAT_ID = -100111
MODERATOR_ID = 9001


def make_app():
    return SimpleNamespace(
        dp=Dispatcher(),
        bot=SimpleNamespace(send_message=AsyncMock()),
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


def comment_message(text: str, *, actor_id=MODERATOR_ID):
    return SimpleNamespace(
        chat=SimpleNamespace(id=MOD_CHAT_ID, type="supergroup"),
        from_user=user(actor_id),
        text=text,
        answer=AsyncMock(),
    )


def handlers_by_name(app, observer_name):
    return {
        item.callback.__name__: item.callback
        for item in getattr(app.dp, observer_name).handlers
    }


class WritersSubmissionHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = [100.0]
        self.app = make_app()
        self.service = make_service()
        self.config = make_config()
        register_writers_submission_handlers(
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
            for item in app.dp.message.handlers[:2]
        ]
        callback_names = [
            item.callback.__name__
            for item in app.dp.callback_query.handlers[:1]
        ]
        self.assertEqual(
            set(message_names),
            {"writers_submission_start", "writers_moderation_comment"},
        )
        self.assertEqual(
            callback_names,
            ["writers_moderation_callback"],
        )


if __name__ == "__main__":
    unittest.main()

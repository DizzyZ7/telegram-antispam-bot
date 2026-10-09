"""Regression checks: moderation must run in every Writers forum thread."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram import Dispatcher, F

from writers_moderation import (
    ProhibitedLanguageFilter,
    WritersChatFilter,
    WritersChatScope,
    promote_writers_moderation_handler,
    register_writers_chat_handlers,
    report_writers_delete_permission,
    WRITERS_PROFANITY_EXEMPT_TOPIC_IDS,
    contains_prohibited_language,
)

WRITERS_CHAT_ID = -1002619489118
# Intentionally exempted by ICФ owner.
EXEMPT_TOPIC_IDS = (14637, 42817, 292358)


def make_app():
    return SimpleNamespace(
        dp=Dispatcher(),
        ALLOWED_CHATS=[WRITERS_CHAT_ID],
        bot=SimpleNamespace(send_message=AsyncMock()),
    )


def make_message(topic_id, text="Это блять недопустимо", *, chat_id=WRITERS_CHAT_ID):
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id, username="chat_ikf"),
        from_user=SimpleNamespace(id=777, is_bot=False),
        text=text,
        caption=None,
        message_id=222,
        message_thread_id=topic_id,
        delete=AsyncMock(),
    )


class WritersProfanityRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_profanity_deleted_in_regular_topics_including_general(self):
        app = make_app()
        register_writers_chat_handlers(app)
        handler = app.dp.message.handlers[0].callback
        scope = WritersChatScope()
        scope.chat_id = WRITERS_CHAT_ID
        scope_filter = WritersChatFilter(scope, app.ALLOWED_CHATS)
        language_filter = ProhibitedLanguageFilter()

        for topic_id in (None, 1, 22):
            with self.subTest(topic_id=topic_id):
                message = make_message(topic_id, text="НИХУЯ СЕБЕ")
                self.assertTrue(await scope_filter(message))
                self.assertTrue(await language_filter(message))
                await handler(message)
                message.delete.assert_awaited_once()

    async def test_exactly_three_topics_remain_exempt_from_profanity_deletion(self):
        self.assertEqual(
            WRITERS_PROFANITY_EXEMPT_TOPIC_IDS,
            frozenset(EXEMPT_TOPIC_IDS),
        )
        app = make_app()
        register_writers_chat_handlers(app)
        scope = WritersChatScope()
        scope.chat_id = WRITERS_CHAT_ID
        scope_filter = WritersChatFilter(scope, app.ALLOWED_CHATS)
        language_filter = ProhibitedLanguageFilter()

        for topic_id in EXEMPT_TOPIC_IDS:
            with self.subTest(topic_id=topic_id):
                message = make_message(topic_id, text="НИХУЯ СЕБЕ")
                self.assertTrue(await scope_filter(message))
                self.assertFalse(await language_filter(message))
                message.delete.assert_not_awaited()

    async def test_nihuya_mat_is_blocked_even_when_uppercase(self):
        for text in ("НИХУЯ СЕБЕ", "Да нихуя себе!", "нихуево"):
            with self.subTest(text=text):
                self.assertTrue(contains_prohibited_language(text))
                self.assertTrue(await ProhibitedLanguageFilter()(make_message(1, text=text)))

    async def test_non_writers_chat_still_out_of_scope_and_safe_text_not_deleted(self):
        scope = WritersChatScope()
        scope.chat_id = WRITERS_CHAT_ID
        scope_filter = WritersChatFilter(scope, [WRITERS_CHAT_ID])
        language_filter = ProhibitedLanguageFilter()
        self.assertFalse(await scope_filter(make_message(22, chat_id=-100123456)))
        self.assertFalse(await language_filter(make_message(22, text="Пишем новую главу")))
        self.assertTrue(await language_filter(make_message(22, text="/блять")))

    async def test_anonymous_admin_profanity_is_moderated_not_skipped_as_bot(self):
        message = make_message(22)
        message.from_user.is_bot = True
        message.sender_chat = SimpleNamespace(id=WRITERS_CHAT_ID)
        self.assertTrue(await ProhibitedLanguageFilter()(message))

        # Ordinary messages from bots stay outside the human profanity filter.
        message.sender_chat = None
        self.assertFalse(await ProhibitedLanguageFilter()(message))

    async def test_newer_catchall_cannot_get_priority_over_writers_moderation(self):
        app = make_app()
        register_writers_chat_handlers(app)

        @app.dp.message(F.text)
        async def recently_registered_catchall(message):
            return None

        app.dp.message.handlers.insert(0, app.dp.message.handlers.pop())
        self.assertEqual(app.dp.message.handlers[0].callback.__name__, "recently_registered_catchall")
        promote_writers_moderation_handler(app.dp)
        self.assertEqual(app.dp.message.handlers[0].callback.__name__, "remove_prohibited_language")

    async def test_missing_delete_permissions_are_logged_clearly(self):
        bot = SimpleNamespace(
            id=123,
            get_chat_member=AsyncMock(return_value=SimpleNamespace(
                status="administrator", can_delete_messages=False
            )),
        )
        with self.assertLogs("writers_moderation", level="ERROR") as logged:
            await report_writers_delete_permission(bot, WRITERS_CHAT_ID)
        self.assertIn("WRITERS_MODERATION_DELETE_PERMISSION_MISSING", logged.output[0])
        bot.get_chat_member.assert_awaited_once_with(chat_id=WRITERS_CHAT_ID, user_id=123)

    async def test_delete_permissions_ok_for_admin_with_delete_right(self):
        bot = SimpleNamespace(
            id=123,
            get_chat_member=AsyncMock(return_value=SimpleNamespace(
                status="administrator", can_delete_messages=True
            )),
        )
        with self.assertLogs("writers_moderation", level="INFO") as logged:
            await report_writers_delete_permission(bot, WRITERS_CHAT_ID)
        self.assertIn("WRITERS_MODERATION_DELETE_PERMISSION_READY", logged.output[0])


class WritersModerationStartupContractTests(unittest.TestCase):
    def test_main_does_not_install_topic_bypass(self):
        from pathlib import Path
        main = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
        self.assertNotIn("install_ignored_topic_filter()", main)
        self.assertIn("promote_writers_moderation_handler(app.dp)", main)
        self.assertIn("WRITERS_TOPIC_EXCLUSIONS_READY ids=", main)


if __name__ == "__main__":
    unittest.main()

"""Lexicon-only group deletes profanity without a single bot chat reply."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram import Dispatcher

from lexicon_game_scope import (
    LEXICON_ONLY_CHAT_IDS,
    SilentLexiconProfanityMiddleware,
    enforce_lexicon_only_isolation,
    register_lexicon_only_guard,
    register_lexicon_silent_profanity_filter,
    report_lexicon_delete_permission,
)
from minigames import MiniGameGuessMiddleware, register_minigame_handlers
from writers_moderation import WRITERS_PROFANITY_EXEMPT_TOPIC_IDS


LEXICON_CHAT = -1002659916114
WRITERS_CHAT = -1002619489118


def message(
    text: str | None,
    *,
    chat_id: int = LEXICON_CHAT,
    caption: str | None = None,
    topic_id: int | None = None,
    is_bot: bool = False,
    anonymous: bool = False,
):
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id),
        from_user=SimpleNamespace(id=4422, is_bot=is_bot),
        sender_chat=SimpleNamespace(id=chat_id) if anonymous else None,
        text=text,
        caption=caption,
        message_thread_id=topic_id,
        message_id=51,
        delete=AsyncMock(),
        answer=AsyncMock(),
        reply=AsyncMock(),
    )


class LexiconSilentProfanityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.middleware = SilentLexiconProfanityMiddleware(LEXICON_ONLY_CHAT_IDS)
        self.next_handler = AsyncMock(return_value="lexicon_game_handled")

    async def test_lexicon_plain_and_masked_profanity_is_deleted_without_bot_reply(self):
        for word in ("блять", "нихуя себе", "бл*ть", "х*й", "п***ц", "f*ck"):
            with self.subTest(word=word):
                msg = message(word)
                self.next_handler.reset_mock()
                result = await self.middleware(self.next_handler, msg, {})
                self.assertIsNone(result)
                msg.delete.assert_awaited_once_with()
                msg.answer.assert_not_awaited()
                msg.reply.assert_not_awaited()
                self.next_handler.assert_not_awaited()

    async def test_photo_caption_is_silently_deleted(self):
        msg = message(None, caption="Вот п***ц")
        await self.middleware(self.next_handler, msg, {})
        msg.delete.assert_awaited_once_with()
        self.next_handler.assert_not_awaited()
        msg.answer.assert_not_awaited()

    async def test_normal_lexicon_guess_and_markdown_pass_unchanged(self):
        for text in ("слово", "/lexicon", "3*5=15", "**Всем привет**"):
            with self.subTest(text=text):
                msg = message(text)
                self.next_handler.reset_mock()
                result = await self.middleware(self.next_handler, msg, {})
                self.assertEqual(result, "lexicon_game_handled")
                msg.delete.assert_not_awaited()
                self.next_handler.assert_awaited_once()

    async def test_other_chats_and_original_writers_exemptions_are_untouched(self):
        for chat in (WRITERS_CHAT, -1001111111111):
            msg = message("бл*ть", chat_id=chat)
            self.next_handler.reset_mock()
            result = await self.middleware(self.next_handler, msg, {})
            self.assertEqual(result, "lexicon_game_handled")
            msg.delete.assert_not_awaited()
        self.assertEqual(
            WRITERS_PROFANITY_EXEMPT_TOPIC_IDS,
            frozenset({14637, 42817, 292358}),
        )
        # Writers topic exemptions are not global exemptions in unrelated chats.
        msg = message("бл*ть", topic_id=14637)
        self.next_handler.reset_mock()
        await self.middleware(self.next_handler, msg, {})
        msg.delete.assert_awaited_once_with()
        self.next_handler.assert_not_awaited()

    async def test_bot_messages_pass_but_anonymous_admin_profanity_is_deleted(self):
        bot = message("блять", is_bot=True)
        result = await self.middleware(self.next_handler, bot, {})
        self.assertEqual(result, "lexicon_game_handled")
        bot.delete.assert_not_awaited()
        anonymous = message("х*й", is_bot=True, anonymous=True)
        self.next_handler.reset_mock()
        await self.middleware(self.next_handler, anonymous, {})
        anonymous.delete.assert_awaited_once_with()
        self.next_handler.assert_not_awaited()

    async def test_delete_permission_failure_is_logged_but_not_replied_or_played(self):
        msg = message("нихуя")
        msg.delete.side_effect = RuntimeError("Telegram permission missing")
        with self.assertLogs("lexicon_game_scope", level="WARNING") as records:
            result = await self.middleware(self.next_handler, msg, {})
        self.assertIsNone(result)
        self.assertIn("LEXICON_PROFANITY_DELETE_FAILED", "\n".join(records.output))
        self.next_handler.assert_not_awaited()
        msg.answer.assert_not_awaited()
        msg.reply.assert_not_awaited()

    async def test_lexicon_delete_permission_diagnostic(self):
        bot = SimpleNamespace(
            id=717,
            get_chat_member=AsyncMock(return_value=SimpleNamespace(
                status="administrator", can_delete_messages=True,
            )),
        )
        with self.assertLogs("lexicon_game_scope", level="INFO") as records:
            await report_lexicon_delete_permission(bot, LEXICON_ONLY_CHAT_IDS)
        bot.get_chat_member.assert_awaited_once_with(
            chat_id=LEXICON_CHAT, user_id=717
        )
        self.assertIn(
            "LEXICON_PROFANITY_DELETE_PERMISSION_READY", "\n".join(records.output)
        )
        bot.get_chat_member.return_value.can_delete_messages = False
        with self.assertLogs("lexicon_game_scope", level="ERROR") as records:
            await report_lexicon_delete_permission(bot, LEXICON_ONLY_CHAT_IDS)
        self.assertIn(
            "LEXICON_PROFANITY_DELETE_PERMISSION_MISSING", "\n".join(records.output)
        )

    def test_outside_game_remains_isolated_and_middleware_runs_before_guessing(self):
        app = SimpleNamespace(
            dp=Dispatcher(),
            ALLOWED_CHATS=[WRITERS_CHAT, LEXICON_CHAT],
        )
        enforce_lexicon_only_isolation(app, LEXICON_ONLY_CHAT_IDS)
        register_lexicon_silent_profanity_filter(app, LEXICON_ONLY_CHAT_IDS)
        register_lexicon_only_guard(app, LEXICON_ONLY_CHAT_IDS)
        register_minigame_handlers(app, SimpleNamespace())
        self.assertNotIn(LEXICON_CHAT, app.ALLOWED_CHATS)
        stack = app.dp.message.outer_middleware._middlewares
        profanity_index = next(
            i for i, m in enumerate(stack)
            if isinstance(m, SilentLexiconProfanityMiddleware)
        )
        guess_index = next(
            i for i, m in enumerate(stack)
            if isinstance(m, MiniGameGuessMiddleware)
        )
        self.assertLess(profanity_index, guess_index)
        self.assertEqual(len(app.dp.callback_query.handlers), 0)
        self.assertEqual(len(app.dp.chat_member.handlers), 0)


if __name__ == "__main__":
    unittest.main()

"""Membership reminders and observed chat-member state for Writers submissions."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from aiogram import Dispatcher

from writers_submission.membership import is_current_member
from writers_submission.runtime import TelegramWritersEligibilityChecker
from writers_submission.service import EligibilityUnavailableError
from writers_submission.handlers import register_writers_submission_handlers

CHAT = -1002619489118


def user_status(status, *, is_member=False, user_id=42):
    return SimpleNamespace(
        status=status, is_member=is_member,
        user=SimpleNamespace(id=user_id, is_bot=False),
    )


class MembershipPolicyTests(unittest.TestCase):
    def test_current_and_departed_members(self):
        for status in ("member", "administrator", "creator", "owner"):
            with self.subTest(status=status):
                self.assertTrue(is_current_member(user_status(status)))
        self.assertTrue(is_current_member(user_status("restricted", is_member=True)))
        for status in ("left", "kicked", "restricted", "unknown"):
            self.assertFalse(is_current_member(user_status(status)))


class MembershipMessageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot = SimpleNamespace(
            get_chat_member=AsyncMock(return_value=user_status("left")),
            get_chat=AsyncMock(return_value=SimpleNamespace(username="ikf_authors", invite_link=None)),
            send_message=AsyncMock(),
        )
        self.registry = SimpleNamespace(
            observe=AsyncMock(),
            claim_join_notice=AsyncMock(side_effect=[True, False]),
        )
        self.checker = TelegramWritersEligibilityChecker(
            self.bot, CHAT, registry=self.registry,
            community_join_url="https://t.me/ikf_authors",
        )

    async def test_denied_submission_sends_warm_private_dm_with_join_button_once(self):
        self.assertFalse(await self.checker(42))
        self.bot.send_message.assert_awaited_once()
        kwargs = self.bot.send_message.await_args.kwargs
        self.assertEqual(kwargs["chat_id"], 42)
        self.assertIn("сохранять", kwargs["text"])
        self.assertIn("ИКФ", kwargs["text"])
        self.assertIn("Отправить на проверку", kwargs["text"])
        self.assertEqual(
            kwargs["reply_markup"].inline_keyboard[0][0].url,
            "https://t.me/ikf_authors",
        )
        self.registry.observe.assert_awaited_once()
        self.assertFalse(await self.checker(42))
        self.bot.send_message.assert_awaited_once()

    async def test_member_is_allowed_and_not_nagged(self):
        self.bot.get_chat_member.return_value = user_status("restricted", is_member=True)
        self.assertTrue(await self.checker(42))
        self.registry.observe.assert_awaited_once()
        self.bot.send_message.assert_not_awaited()

    async def test_unavailable_telegram_neither_marks_nonmember_nor_sends_dm(self):
        self.bot.get_chat_member.side_effect = RuntimeError("Telegram API unavailable")
        with self.assertRaises(EligibilityUnavailableError):
            await self.checker(42)
        self.bot.send_message.assert_not_awaited()
        self.registry.observe.assert_not_awaited()

    async def test_blocked_dm_does_not_break_membership_denial(self):
        self.bot.send_message.side_effect = RuntimeError("Forbidden: blocked")
        self.assertFalse(await self.checker(42))

    async def test_no_invite_link_does_not_create_a_fake_url(self):
        checker = TelegramWritersEligibilityChecker(
            self.bot, CHAT, registry=self.registry,
        )
        self.bot.get_chat.return_value = SimpleNamespace(
            username=None, invite_link=None,
        )
        self.assertFalse(await checker(42))
        self.assertIsNone(self.bot.send_message.await_args.kwargs["reply_markup"])


class MembershipEventTests(unittest.IsolatedAsyncioTestCase):
    async def test_chat_member_events_record_only_target_chat(self):
        bot = SimpleNamespace()
        dp = Dispatcher()
        registry = SimpleNamespace(observe=AsyncMock())
        config = SimpleNamespace(
            moderation_chat_id=555, moderator_ids=frozenset({555}),
            public_url="https://example.test/writers/",
        )
        app = SimpleNamespace(dp=dp, bot=bot)
        register_writers_submission_handlers(
            app, SimpleNamespace(), config,
            membership_registry=registry, membership_chat_id=CHAT,
        )
        handlers = {
            item.callback.__name__: item.callback
            for item in dp.chat_member.handlers
        }
        handler = handlers["writers_community_member_change"]
        event = SimpleNamespace(
            chat=SimpleNamespace(id=CHAT),
            new_chat_member=user_status("member", user_id=42),
        )
        await handler(event)
        registry.observe.assert_awaited_once()
        self.assertEqual(registry.observe.await_args.kwargs["source"], "event")
        registry.observe.reset_mock()
        event.chat.id = 123
        await handler(event)
        registry.observe.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()

"""IKF membership snapshots: only observed users, never enumerated subscribers."""
from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram import Dispatcher

from writers_submission.membership import (
    membership_state, register_community_membership_handler,
)
from writers_submission.runtime import TelegramWritersEligibilityChecker

CHAT = -1002619489118


class MemberStatusTests(unittest.TestCase):
    def test_member_and_restricted_flags_are_explicit(self):
        self.assertEqual(membership_state(SimpleNamespace(status="member")), ("member", True))
        self.assertEqual(membership_state(SimpleNamespace(status="restricted", is_member=True)), ("restricted", True))
        self.assertEqual(membership_state(SimpleNamespace(status="restricted", is_member=False)), ("restricted", False))
        self.assertEqual(membership_state(SimpleNamespace(status="left")), ("left", False))


class MemberLookupTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_lookup_records_status_in_db_for_known_user(self):
        store = SimpleNamespace(record_community_member=AsyncMock())
        bot = SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace(
            status="restricted", is_member=True,
        )))
        checker = TelegramWritersEligibilityChecker(bot, CHAT, storage=store)
        self.assertTrue(await checker(77))
        store.record_community_member.assert_awaited_once()
        args = store.record_community_member.await_args.kwargs
        self.assertEqual((args["chat_id"], args["user_id"], args["status"], args["is_member"], args["source"]),
                         (CHAT, 77, "restricted", True, "live_lookup"))

    async def test_audit_storage_outage_does_not_override_live_authorization(self):
        store = SimpleNamespace(record_community_member=AsyncMock(
            side_effect=RuntimeError("db unavailable")
        ))
        bot = SimpleNamespace(get_chat_member=AsyncMock(
            return_value=SimpleNamespace(status="member")
        ))
        checker = TelegramWritersEligibilityChecker(bot, CHAT, storage=store)
        self.assertTrue(await checker(88))


class MemberUpdatesTests(unittest.IsolatedAsyncioTestCase):
    async def test_joins_leaves_are_recorded_and_other_channels_ignored(self):
        app = SimpleNamespace(dp=Dispatcher())
        storage = SimpleNamespace(record_community_member=AsyncMock())
        register_community_membership_handler(app, storage, CHAT)
        registered = app.dp.chat_member.handlers
        self.assertEqual(len(registered), 1)
        handler = registered[0].callback
        self.assertIn("chat_member", app.dp.resolve_used_update_types())
        def update(user_id=77, status="left", chat_id=CHAT):
            return SimpleNamespace(
                chat=SimpleNamespace(id=chat_id),
                new_chat_member=SimpleNamespace(
                    status=status, user=SimpleNamespace(id=user_id, is_bot=False),
                ),
                date=datetime(2026, 10, 13, tzinfo=timezone.utc),
            )
        await handler(update(status="member"))
        await handler(update(status="left"))
        await handler(update(chat_id=CHAT-1))
        self.assertEqual(storage.record_community_member.await_count, 2)
        self.assertEqual(
            [x.kwargs["is_member"] for x in storage.record_community_member.await_args_list],
            [True, False],
        )
        self.assertEqual(storage.record_community_member.await_args.kwargs["source"], "chat_member_update")


if __name__ == "__main__":
    unittest.main()

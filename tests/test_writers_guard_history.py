"""Writers guardian: evidence-only strike history and authorized review."""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram import Dispatcher

from writers_guard_history import WritersModerationLedger
from writers_moderation import (
    build_warning_text, promote_writers_moderation_handler,
    register_writers_chat_handlers,
)

CHAT = -1002619489118
T0 = 2_000_000


class GuardHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "guards.db"
        self.ledger = WritersModerationLedger(self.path)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_counts_only_confirmed_distinct_deletions_and_persists(self):
        first = await self.ledger.record_deleted_message(
            chat_id=CHAT, user_id=42, message_id=111, at=T0,
        )
        again = await self.ledger.record_deleted_message(
            chat_id=CHAT, user_id=42, message_id=111, at=T0,
        )
        second = await self.ledger.record_deleted_message(
            chat_id=CHAT, user_id=42, message_id=112, at=T0+4,
        )
        other = await self.ledger.record_deleted_message(
            chat_id=CHAT, user_id=45, message_id=113, at=T0+5,
        )
        self.assertEqual((first.recent_count, first.newly_recorded), (1, True))
        self.assertEqual((again.recent_count, again.newly_recorded), (1, False))
        self.assertEqual((second.recent_count, second.newly_recorded), (2, True))
        self.assertEqual(other.recent_count, 1)
        new_instance = WritersModerationLedger(self.path)
        self.assertEqual(await new_instance.recent_count(CHAT, 42, at=T0+7), 2)
        self.assertEqual(await new_instance.recent_count(CHAT+1, 42, at=T0+7), 0)
        self.assertEqual(await new_instance.clear_user(CHAT, 42), 2)
        self.assertEqual(await new_instance.recent_count(CHAT, 42, at=T0+7), 0)
        self.assertEqual(await new_instance.recent_count(CHAT, 45, at=T0+7), 1)

    async def test_expiry_and_short_repeat_window(self):
        await self.ledger.record_deleted_message(
            chat_id=CHAT, user_id=42, message_id=1, at=T0,
        )
        # 8 days old no longer escalates.
        result = await self.ledger.record_deleted_message(
            chat_id=CHAT, user_id=42, message_id=2, at=T0+8*86400,
        )
        self.assertEqual(result.recent_count, 1)
        self.assertEqual(await self.ledger.recent_count(
            CHAT,42,at=T0+8*86400), 1)
        await self.ledger.record_deleted_message(
            chat_id=CHAT, user_id=42, message_id=3, at=T0+32*86400,
        )
        db = await self.ledger._open()
        try:
            async with db.execute(
                "SELECT COUNT(*),MIN(occurred_at) FROM writers_guard_violations"
            ) as q:
                count, oldest = await q.fetchone()
            self.assertEqual(count,1)
            self.assertEqual(oldest,T0+32*86400)
        finally:
            await db.close()

    async def test_parallel_workers_dedupe_same_message(self):
        results = await asyncio.gather(*[
            self.ledger.record_deleted_message(
                chat_id=CHAT, user_id=42, message_id=111, at=T0
            ) for _ in range(5)
        ])
        self.assertEqual(sum(item.newly_recorded for item in results), 1)
        self.assertTrue(all(item.recent_count == 1 for item in results))

    def test_warning_copy_escalates_but_never_demeans_people(self):
        self.assertIn("Мат", build_warning_text())
        self.assertIn("Повторное нарушение", build_warning_text(2))
        self.assertIn("серьезное предупреждение", build_warning_text(3))
        self.assertNotIn("@", build_warning_text(3))


class GuardHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = WritersModerationLedger(Path(self.tmp.name)/"guard.db")
        self.bot = SimpleNamespace(
            send_message=AsyncMock(),
            get_chat_member=AsyncMock(return_value=SimpleNamespace(status="administrator")),
        )
        self.app = SimpleNamespace(
            dp=Dispatcher(), ALLOWED_CHATS=[], bot=self.bot,
        )
        register_writers_chat_handlers(self.app, moderation_ledger=self.ledger)
        promote_writers_moderation_handler(self.app.dp)
        self.callbacks = {
            item.callback.__name__:item.callback
            for item in self.app.dp.message.handlers
        }

    async def asyncTearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def message(message_id, sender=42, reply_to=None):
        return SimpleNamespace(
            chat=SimpleNamespace(id=CHAT),
            from_user=SimpleNamespace(id=sender,is_bot=False),
            sender_chat=None,message_id=message_id,
            message_thread_id=15, reply_to_message=reply_to,
            reply=AsyncMock(),
            delete=AsyncMock(),
        )

    async def test_escalation_dedup_and_no_count_if_delete_fails(self):
        delete = self.callbacks["remove_prohibited_language"]
        first = self.message(1)
        await delete(first)
        second = self.message(2)
        await delete(second)
        self.assertEqual(await self.ledger.recent_count(CHAT, 42), 2)
        self.assertIn("Повторное нарушение", self.bot.send_message.call_args.kwargs["text"])
        await delete(second)
        self.assertEqual(await self.ledger.recent_count(CHAT, 42), 2)

        failed = self.message(3)
        failed.delete.side_effect = PermissionError("cannot delete")
        await delete(failed)
        self.assertEqual(await self.ledger.recent_count(CHAT, 42), 2)

    async def test_admin_only_status_and_reset(self):
        target = self.message(10)
        query = self.message(11, sender=55, reply_to=target)
        await self.ledger.record_deleted_message(
            chat_id=CHAT, user_id=42, message_id=10,
        )
        await self.callbacks["guard_status"](query)
        self.assertIn("1", query.reply.call_args.args[0])
        await self.callbacks["guard_reset"](query)
        self.assertEqual(await self.ledger.recent_count(CHAT,42),0)
        self.bot.get_chat_member.return_value = SimpleNamespace(status="member")
        query.reply.reset_mock()
        await self.callbacks["guard_status"](query)
        query.reply.assert_not_awaited()

    def test_guard_handlers_have_priority_below_delete(self):
        names = [h.callback.__name__ for h in self.app.dp.message.handlers]
        self.assertEqual(names[:3],[
            "remove_prohibited_language","guard_status","guard_reset",
        ])


if __name__ == "__main__":
    unittest.main()

"""Private, manually reviewed chat reports; allegations never become strikes."""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram import Dispatcher

from writers_guard_reports import (
    ReportRateLimited, WritersReportQueue,
)
from writers_guard_history import WritersModerationLedger
from writers_moderation import (
    register_writers_chat_handlers, promote_writers_moderation_handler,
)

CHAT = -1002619489118
WHEN = 2_000_000


class ReportStorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/"reports.db"
        self.queue = WritersReportQueue(self.path)

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def submit(self, msg, *, reporter=101, chat=CHAT, at=WHEN):
        return await self.queue.submit(
            chat_id=chat, topic_id=77, target_message_id=msg,
            target_user_id=202, reporter_user_id=reporter,
            reason="spam", at=at,
        )

    async def test_submission_deduplication_and_restart(self):
        first=await self.submit(1)
        same=await self.submit(1)
        self.assertTrue(first.created)
        self.assertFalse(same.created)
        self.assertEqual(first.report_id,same.report_id)
        fresh=WritersReportQueue(self.path)
        reports,total=await fresh.pending(CHAT,at=WHEN)
        self.assertEqual(total,1)
        self.assertEqual(reports[0].reason,"spam")
        self.assertEqual(reports[0].target_message_id,1)
        self.assertEqual(reports[0].reporter_user_id,101)
        self.assertEqual(reports[0].status,"pending")

    async def test_three_per_day_per_reporter_and_other_reporters_unaffected(self):
        for msg in (1,2,3):
            self.assertTrue((await self.submit(msg)).created)
        with self.assertRaises(ReportRateLimited):
            await self.submit(4)
        self.assertTrue((await self.submit(4,reporter=103)).created)
        self.assertTrue((await self.submit(4,at=WHEN+86401)).created)

    async def test_reports_are_allegations_and_not_strikes(self):
        ledger=WritersModerationLedger(Path(self.temp.name)/"guards.db")
        await self.submit(1)
        self.assertEqual(await ledger.recent_count(CHAT,202,at=WHEN),0)
        report=(await self.queue.pending(CHAT,at=WHEN))[0][0]
        self.assertTrue(await self.queue.decide(
            chat_id=CHAT,report_id=report.id,admin_user_id=501,
            decision="confirmed",at=WHEN+1,
        ))
        self.assertEqual(await ledger.recent_count(CHAT,202,at=WHEN+1),0)

    async def test_double_review_unknown_id_and_cross_chat_denied(self):
        x=await self.submit(1)
        self.assertFalse(await self.queue.decide(
            chat_id=CHAT+1,report_id=x.report_id,
            admin_user_id=900,decision="confirmed",at=WHEN+1,
        ))
        self.assertTrue(await self.queue.decide(
            chat_id=CHAT,report_id=x.report_id,
            admin_user_id=900,decision="dismissed",at=WHEN+1,
        ))
        self.assertFalse(await self.queue.decide(
            chat_id=CHAT,report_id=x.report_id,
            admin_user_id=900,decision="confirmed",at=WHEN+2,
        ))
        self.assertEqual((await self.queue.pending(CHAT,at=WHEN+2))[1],0)

    async def test_invalid_reason_self_report_and_expiry(self):
        with self.assertRaises(ValueError):
            await self.queue.submit(
                chat_id=CHAT,topic_id=1,target_message_id=1,
                target_user_id=101,reporter_user_id=101,reason="other",at=WHEN,
            )
        with self.assertRaises(ValueError):
            await self.queue.submit(
                chat_id=CHAT,topic_id=1,target_message_id=1,
                target_user_id=202,reporter_user_id=101,reason="secret",at=WHEN,
            )
        await self.submit(1)
        self.assertEqual((await self.queue.pending(CHAT,at=WHEN+31*86400))[1],0)

    async def test_concurrent_duplicate_submissions_are_atomic(self):
        found=await asyncio.gather(*[self.submit(1) for _ in range(5)])
        self.assertEqual(sum(x.created for x in found),1)
        self.assertEqual((await self.queue.pending(CHAT,at=WHEN))[1],1)


class ReportHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.queue=WritersReportQueue(Path(self.tmp.name)/"reports.db")
        self.bot=SimpleNamespace(
            send_message=AsyncMock(),
            get_chat_member=AsyncMock(return_value=SimpleNamespace(status="administrator")),
        )
        self.app=SimpleNamespace(dp=Dispatcher(),ALLOWED_CHATS=[],bot=self.bot)
        register_writers_chat_handlers(self.app,report_queue=self.queue)
        promote_writers_moderation_handler(self.app.dp)
        self.handlers={item.callback.__name__:item.callback
                       for item in self.app.dp.message.handlers}

    async def asyncTearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def message(mid, *, sender=101, text="/report", reply_to=None,
                topic=77, sender_chat=None):
        return SimpleNamespace(
            chat=SimpleNamespace(id=CHAT),
            from_user=SimpleNamespace(id=sender,is_bot=False),
            sender_chat=sender_chat,message_id=mid,
            message_thread_id=topic,text=text,
            reply_to_message=reply_to,reply=AsyncMock(),
        )

    async def test_report_by_reply_only_and_no_self_report(self):
        original=self.message(70,sender=202,text="это пример")
        invalid=self.message(71,reply_to=None)
        await self.handlers["report_message"](invalid)
        self.assertIn("ответом", invalid.reply.call_args.args[0])
        own=self.message(72,reply_to=self.message(69,sender=101))
        await self.handlers["report_message"](own)
        self.assertIn("другого",own.reply.call_args.args[0])
        sent=self.message(73,reply_to=original,text="/report spam")
        await self.handlers["report_message"](sent)
        self.assertIn("принята",sent.reply.call_args.args[0])
        self.assertEqual((await self.queue.pending(CHAT))[1],1)
        same=self.message(74,reply_to=original,text="/report spam")
        await self.handlers["report_message"](same)
        self.assertIn("уже зарегистрирована",same.reply.call_args.args[0])
        self.assertEqual((await self.queue.pending(CHAT))[1],1)

    async def test_admin_list_delivered_privately_only(self):
        original=self.message(70,sender=202)
        await self.handlers["report_message"](
            self.message(71,reply_to=original,text="/report insult")
        )
        command=self.message(80,sender=301,text="/reports")
        await self.handlers["list_reports"](command)
        self.bot.send_message.assert_awaited_once()
        args=self.bot.send_message.call_args.kwargs
        self.assertEqual(args["chat_id"],301)
        self.assertIn("сообщение 70",args["text"])
        self.assertNotIn("пример",args["text"])
        self.assertNotIn("сообщение 70",command.reply.call_args.args[0])
        # A failed DM must not leak details into the public chat.
        self.bot.send_message.side_effect=RuntimeError("no PM")
        failed=self.message(81,sender=301,text="/reports")
        await self.handlers["list_reports"](failed)
        self.assertNotIn("сообщение 70",failed.reply.call_args.args[0])

    async def test_nonadmin_cannot_list_or_review(self):
        original=self.message(70,sender=202)
        await self.handlers["report_message"](self.message(71,reply_to=original))
        item=(await self.queue.pending(CHAT))[0][0]
        self.bot.get_chat_member.return_value=SimpleNamespace(status="member")
        who=self.message(81,sender=301,text="/reports")
        await self.handlers["list_reports"](who)
        who.reply.assert_not_awaited()
        attempt=self.message(82,sender=301,text=f"/report_confirm {item.id}")
        await self.handlers["confirm_report"](attempt)
        attempt.reply.assert_not_awaited()
        self.assertEqual((await self.queue.pending(CHAT))[1],1)

    async def test_admin_confirms_once_without_auto_delete_or_ban(self):
        original=self.message(70,sender=202)
        await self.handlers["report_message"](self.message(71,reply_to=original))
        item=(await self.queue.pending(CHAT))[0][0]
        cmd=self.message(81,sender=301,text=f"/report_confirm {item.id}")
        await self.handlers["confirm_report"](cmd)
        self.assertIn("подтверждена",cmd.reply.call_args.args[0])
        self.assertEqual((await self.queue.pending(CHAT))[1],0)
        again=self.message(82,sender=301,text=f"/report_dismiss {item.id}")
        await self.handlers["dismiss_report"](again)
        self.assertIn("уже рассмотрена",again.reply.call_args.args[0])
        self.bot.send_message.assert_not_awaited()

    def test_moderation_first_and_report_commands_follow(self):
        names=[x.callback.__name__ for x in self.app.dp.message.handlers]
        self.assertEqual(names[:7],[
            "remove_prohibited_language","guard_status","guard_reset",
            "report_message","list_reports","confirm_report","dismiss_report",
        ])


if __name__ == "__main__":
    unittest.main()

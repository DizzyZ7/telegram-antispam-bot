"""One-time Pupsan birthday sender: startup, Telegram and PostgreSQL guarantees."""
from __future__ import annotations

import asyncio
import json
import os
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.methods import SendMessage

from pupsan_oneshot import (
    CHAT_ID, DEADLINE, EVENT_ID, MOMENT, SOURCE_MESSAGE_ID, TEXT,
    PostgresOneShotJournal, send_pupsan_once,
)


def moscow(day: int = 24, hour: int = 19, minute: int = 5) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=ZoneInfo("Europe/Moscow"))


class OneShotTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.journal = SimpleNamespace(
            claim=AsyncMock(return_value=(True, "CLAIMED", None)),
            finish=AsyncMock(),
        )
        self.bot = SimpleNamespace(send_message=AsyncMock(
            return_value=SimpleNamespace(message_id=586900, message_thread_id=1)
        ))

    async def test_direct_send_uses_original_topic_reply_and_records_message(self):
        result = await send_pupsan_once(self.bot, None, when=moscow(), journal=self.journal)
        self.assertEqual(result, "sent")
        self.bot.send_message.assert_awaited_once()
        options = self.bot.send_message.call_args.kwargs
        self.assertEqual(options["chat_id"], CHAT_ID)
        self.assertIsNone(options["parse_mode"])
        self.assertEqual(options["reply_parameters"].message_id, SOURCE_MESSAGE_ID)
        self.assertEqual(options["text"], TEXT)
        self.journal.finish.assert_awaited_once_with(
            "SENT", int(moscow().timestamp()), 586900,
        )

    async def test_no_repeat_when_prior_receipt_exists(self):
        for status in ("SENT", "CLAIMED", "UNCERTAIN"):
            with self.subTest(status=status):
                self.journal.claim.return_value = (False, status, 586900)
                result = await send_pupsan_once(self.bot, None, when=moscow(), journal=self.journal)
                self.assertEqual(result, "already_claimed")
        self.bot.send_message.assert_not_awaited()
        self.journal.finish.assert_not_awaited()

    async def test_invalid_reply_falls_back_to_plain_chat(self):
        self.bot.send_message.side_effect = [
            TelegramBadRequest(method=SendMessage(chat_id=CHAT_ID, text="x"), message="reply not found"),
            SimpleNamespace(message_id=586901, message_thread_id=None),
        ]
        result = await send_pupsan_once(self.bot, None, when=moscow(), journal=self.journal)
        self.assertEqual(result, "sent")
        self.assertEqual(self.bot.send_message.await_count, 2)
        self.assertIn("reply_parameters", self.bot.send_message.await_args_list[0].kwargs)
        self.assertNotIn("reply_parameters", self.bot.send_message.await_args_list[1].kwargs)
        self.journal.finish.assert_awaited_once_with("SENT", int(moscow().timestamp()), 586901)

    async def test_network_unknown_is_not_retried(self):
        self.bot.send_message.side_effect = TimeoutError("outcome unknown")
        result = await send_pupsan_once(self.bot, None, when=moscow(), journal=self.journal)
        self.assertEqual(result, "uncertain")
        self.assertEqual(self.bot.send_message.await_count, 1)
        self.journal.finish.assert_awaited_once_with("UNCERTAIN", int(moscow().timestamp()), None)

    async def test_confirmed_rejection_is_recorded(self):
        self.bot.send_message.side_effect = TelegramForbiddenError(
            method=SendMessage(chat_id=CHAT_ID, text="x"), message="bot not allowed"
        )
        result = await send_pupsan_once(self.bot, None, when=moscow(), journal=self.journal)
        self.assertEqual(result, "rejected")
        self.journal.finish.assert_awaited_once_with("REJECTED", int(moscow().timestamp()), None)

    async def test_date_and_time_scoped_strictly(self):
        for at in (moscow(hour=13,minute=19), moscow(day=23), moscow(day=25, hour=0)):
            with self.subTest(at=at):
                self.assertEqual(
                    await send_pupsan_once(self.bot, None, when=at, journal=self.journal),
                    "outside_window",
                )
        self.bot.send_message.assert_not_awaited()
        self.journal.claim.assert_not_awaited()

    async def test_never_send_without_persistent_database(self):
        self.assertEqual(
            await send_pupsan_once(self.bot, None, when=moscow()), "no_database"
        )
        self.bot.send_message.assert_not_awaited()

    async def test_database_claim_failure_never_sends_untracked(self):
        self.journal.claim.side_effect = RuntimeError("database unreachable")
        self.assertEqual(
            await send_pupsan_once(self.bot, None, when=moscow(), journal=self.journal),
            "storage_error",
        )
        self.bot.send_message.assert_not_awaited()

    def test_text_is_exact_existing_personal_message(self):
        source = Path(__file__).resolve().parents[1] / "entertainment" / "birthday_plans.json"
        plans = json.loads(source.read_text(encoding="utf-8"))
        plan = next(x for x in plans if x["username"] == "pupsan99")
        self.assertEqual(TEXT, plan["text"])
        self.assertIn("Ты создала", TEXT)
        self.assertIn("Еноту пора!", TEXT)
        self.assertLessEqual(len(TEXT), 4096)

    def test_startup_hook_precedes_entertainment_supervisor(self):
        source = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
        self.assertIn("await send_pupsan_once(app.bot", source)
        self.assertLess(source.index("await send_pupsan_once(app.bot"), source.index("entertainment_supervisor.start()"))
        self.assertIn("if plan.event_id != personal_birthday_id", source)


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL not configured")
class OneShotPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import asyncpg
        self.dsn = os.environ["TEST_DATABASE_URL"]
        self.journal = PostgresOneShotJournal(self.dsn)
        self.connection = await asyncpg.connect(self.dsn)
        await self.connection.execute(
            """CREATE TABLE IF NOT EXISTS fosgen_one_shot_delivery (
                event_id TEXT PRIMARY KEY,status TEXT NOT NULL,
                updated_at BIGINT NOT NULL,telegram_message_id BIGINT
            )"""
        )
        await self.connection.execute(
            "DELETE FROM fosgen_one_shot_delivery WHERE event_id=$1", EVENT_ID
        )

    async def asyncTearDown(self):
        await self.connection.execute(
            "DELETE FROM fosgen_one_shot_delivery WHERE event_id=$1", EVENT_ID
        )
        await self.connection.close()

    async def test_concurrent_workers_claim_once_and_persist_ack(self):
        instant = int(moscow().timestamp())
        claims = await asyncio.gather(*[self.journal.claim(instant) for _ in range(8)])
        self.assertEqual(sum(1 for claimed,_,_ in claims if claimed), 1)
        await self.journal.finish("SENT", instant, 586900)
        self.assertEqual(await self.journal.claim(instant), (False, "SENT", 586900))
        another_worker = PostgresOneShotJournal(self.dsn)
        self.assertEqual(await another_worker.claim(instant), (False, "SENT", 586900))

    async def test_confirmed_rejection_can_retry_but_uncertain_cannot(self):
        instant = int(moscow().timestamp())
        self.assertTrue((await self.journal.claim(instant))[0])
        await self.journal.finish("REJECTED", instant, None)
        self.assertTrue((await self.journal.claim(instant))[0])
        await self.journal.finish("UNCERTAIN", instant, None)
        self.assertEqual(await self.journal.claim(instant), (False, "UNCERTAIN", None))


if __name__ == "__main__":
    unittest.main()

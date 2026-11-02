from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from aiogram.exceptions import TelegramBadRequest, TelegramServerError
from aiogram.methods import SendMessage

from entertainment.birthdays import BirthdayCoordinator, BirthdayPlan, load_birthday_plans
import entertainment.birthdays as birthday_module
from entertainment import EntertainmentService
from entertainment.storage.sqlite import SQLiteEntertainmentStorage
from entertainment.storage.postgres import PostgresEntertainmentStorage

CHAT = -1002619489118


def timestamp(day=3, hour=12, minute=30):
    return int(datetime(2026, 11, day, hour, minute, tzinfo=ZoneInfo("Europe/Moscow")).timestamp())


def plan():
    return BirthdayPlan(event_id="birthday-test", chat_id=CHAT,
                        username="StorytellerExample", local_date="2026-11-03",
                        send_time="12:30", text="@StorytellerExample, с днем рождения! 🎂\n\nЖелаю добрых сказок и счастья. 💛")


def message(text="@StorytellerExample, с днем рождения!", **extra):
    values = dict(chat=SimpleNamespace(id=CHAT, type="supergroup"),
                  from_user=SimpleNamespace(id=7, is_bot=False, username="someone_else"),
                  text=text, caption=None, message_id=10, message_thread_id=55,
                  reply_to_message=None, entities=[], forward_origin=None,
                  date=datetime.fromtimestamp(timestamp(hour=9), timezone.utc))
    values.update(extra)
    return SimpleNamespace(**values)


class _BirthdayStorageCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "birthdays.db"
        self.storage = SQLiteEntertainmentStorage(self.path)
        await self.storage.initialize()
        self.bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=99)))
        self.coordinator = BirthdayCoordinator(self.bot, self.storage, {CHAT}, [plan()])

    async def asyncTearDown(self):
        await self.storage.close()
        self.directory.cleanup()


class BirthdayTests(_BirthdayStorageCase):
    async def test_timer_uses_moscow_time_and_deduplicates_after_restart(self):
        await self.coordinator.tick(timestamp(minute=29))
        self.assertEqual(self.bot.send_message.await_count, 0)
        await self.coordinator.tick(timestamp())
        self.assertEqual(self.bot.send_message.await_count, 1)
        self.assertEqual(self.bot.send_message.call_args.kwargs["chat_id"], CHAT)
        await self.storage.close()
        self.storage = SQLiteEntertainmentStorage(self.path)
        await self.storage.initialize()
        restarted = BirthdayCoordinator(self.bot, self.storage, {CHAT}, [plan()])
        await restarted.tick(timestamp(hour=13))
        self.assertEqual(self.bot.send_message.await_count, 1)

    async def test_exact_recipient_can_trigger_early_and_suppresses_timer(self):
        self.assertTrue(await self.coordinator.observe(message(), timestamp(hour=9)))
        self.assertEqual(self.bot.send_message.call_args.kwargs["message_thread_id"], 55)
        self.assertTrue(await self.coordinator.observe(message(), timestamp(hour=10)))
        await self.coordinator.tick(timestamp())
        self.assertEqual(self.bot.send_message.await_count, 1)

    async def test_wrong_or_ambiguous_recipient_never_triggers_early(self):
        for item in (
            message("С днем рождения!"), message("@SomeoneElse, с днем рождения!"),
            message("@StorytellerExampleToo, с днем рождения!"),
            message("@StorytellerExample, поздравляю с победой!"),
            message("@StorytellerExample, с днем рождения! @SomeoneElse тоже"),
            message(chat=SimpleNamespace(id=-999, type="supergroup")),
            message(from_user=SimpleNamespace(id=9, is_bot=True)),
            message(forward_origin=SimpleNamespace()),
            message(from_user=SimpleNamespace(id=8, is_bot=False, username="StorytellerExample")),
        ):
            await self.coordinator.observe(item, timestamp(hour=9))
        self.assertEqual(self.bot.send_message.await_count, 0)

    async def test_reply_to_recipient_matches_even_without_at_mention(self):
        reply = SimpleNamespace(from_user=SimpleNamespace(id=8, username="STORYTELLEREXAMPLE"))
        self.assertTrue(await self.coordinator.observe(message("С днем рождения!", reply_to_message=reply), timestamp(hour=10)))
        self.assertEqual(self.bot.send_message.await_count, 1)

    async def test_conflicting_reply_and_unresolved_text_mention_do_not_trigger(self):
        reply = SimpleNamespace(from_user=SimpleNamespace(id=8, username="StorytellerExample"))
        await self.coordinator.observe(message("@SomeoneElse, с днем рождения!", reply_to_message=reply), timestamp(hour=10))
        entity = SimpleNamespace(type="text_mention", user=SimpleNamespace(id=123, username=None))
        await self.coordinator.observe(message("@StorytellerExample и Маша, с днем рождения!", entities=[entity]), timestamp(hour=10))
        self.assertEqual(self.bot.send_message.await_count, 0)

    async def test_backlogged_message_must_have_the_birthday_date_itself(self):
        old = datetime.fromtimestamp(timestamp(day=2, hour=23, minute=59), timezone.utc)
        future = datetime.fromtimestamp(timestamp(day=4, hour=0, minute=1), timezone.utc)
        self.assertFalse(await self.coordinator.observe(message(date=old), timestamp(hour=0, minute=1)))
        self.assertFalse(await self.coordinator.observe(message(date=future), timestamp(hour=23, minute=59)))
        self.assertEqual(self.bot.send_message.await_count, 0)

    async def test_different_plans_share_one_daily_slot_across_topics(self):
        second = BirthdayPlan(event_id="another-birthday", chat_id=CHAT,
                              username="AnotherExample", local_date="2026-11-03",
                              send_time="12:30", text="@AnotherExample, с днем рождения!")
        coordinator = BirthdayCoordinator(self.bot, self.storage, {CHAT}, [plan(), second])
        await coordinator.tick(timestamp())
        self.assertEqual(self.bot.send_message.await_count, 1)

    async def test_other_dates_and_year_do_not_send(self):
        for now in (timestamp(day=2, hour=23), timestamp(day=4, hour=0), timestamp()+365*86400):
            await self.coordinator.tick(now)
            self.assertFalse(await self.coordinator.observe(message(), now))
        self.assertEqual(self.bot.send_message.await_count, 0)

    async def test_message_and_timer_race_only_send_once(self):
        await asyncio.gather(self.coordinator.tick(timestamp()),
                             self.coordinator.observe(message(), timestamp()))
        self.assertEqual(self.bot.send_message.await_count, 1)

    async def test_uncertain_network_result_is_not_retried_blindly(self):
        self.bot.send_message.side_effect = TimeoutError("Telegram outcome unknown")
        await self.coordinator.tick(timestamp())
        await self.coordinator.tick(timestamp(minute=31))
        self.assertEqual(self.bot.send_message.await_count, 1)

    async def test_definite_telegram_rejection_can_retry(self):
        self.bot.send_message.side_effect = [TelegramBadRequest(method=SendMessage(chat_id=CHAT, text="test"), message="rejected"), SimpleNamespace(message_id=99)]
        await self.coordinator.tick(timestamp())
        await self.coordinator.tick(timestamp(minute=31))
        self.assertEqual(self.bot.send_message.await_count, 2)

    async def test_telegram_server_error_is_uncertain_and_cannot_duplicate(self):
        self.bot.send_message.side_effect = TelegramServerError(method=SendMessage(chat_id=CHAT, text="test"), message="upstream failure")
        await self.coordinator.tick(timestamp())
        await self.coordinator.tick(timestamp(minute=31))
        self.assertEqual(self.bot.send_message.await_count, 1)

    async def test_production_topic_denylist_still_applies(self):
        service = EntertainmentService(SimpleNamespace(bot=self.bot), self.storage, {CHAT},
                                       now_fn=lambda: timestamp(hour=9), birthday_plans=[plan()])
        await service.observe_message(message(message_thread_id=292358))
        self.assertEqual(self.bot.send_message.await_count, 0)

    async def test_service_supervisor_sends_even_without_active_topic(self):
        service = EntertainmentService(SimpleNamespace(bot=self.bot), self.storage, {CHAT},
                                       now_fn=lambda: timestamp(), birthday_plans=[plan()])
        await service.run_supervisor_tick()
        self.assertEqual(self.bot.send_message.await_count, 1)

    async def test_service_targeted_message_bypasses_randomness_and_generic_reply(self):
        service = EntertainmentService(SimpleNamespace(bot=self.bot), self.storage, {CHAT},
                                       now_fn=lambda: timestamp(hour=9), birthday_plans=[plan()])
        service._maybe_celebrate = AsyncMock(side_effect=AssertionError("must not send generic celebration"))
        await service.observe_message(message())
        self.assertEqual(self.bot.send_message.await_count, 1)

    def test_user_plan_is_one_off_and_contains_warm_storyteller_text(self):
        plans = load_birthday_plans()
        matching = [p for p in plans if p.username == "Buketsireni" and p.local_date == "2026-11-03"]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0].chat_id, CHAT)
        self.assertEqual(matching[0].send_time, "12:30")
        self.assertIn("@Buketsireni", matching[0].text)
        self.assertIn("сказ", matching[0].text.casefold())
        self.assertIn("\n\n", matching[0].text)
        self.assertLess(len(matching[0].text), 4096)


class GenericBirthdayTests(_BirthdayStorageCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.coordinator = BirthdayCoordinator(self.bot, self.storage, {CHAT}, [])

    async def test_first_birthday_phrase_triggers_once_and_repeated_phrases_do_not_spam(self):
        await self.coordinator.observe(message("С днем рождения!"), timestamp(hour=9))
        self.assertEqual(self.bot.send_message.await_count, 1)
        await self.coordinator.observe(message("С ДР!"), timestamp(hour=9))
        self.assertEqual(self.bot.send_message.await_count, 1)
        other = SimpleNamespace(id=11, is_bot=False, username="another_person")
        await self.coordinator.observe(message("С днем рождения!", from_user=other), timestamp(hour=9))
        self.assertEqual(self.bot.send_message.await_count, 1)
        self.assertIn("С днем рождения", self.bot.send_message.call_args.kwargs["text"])

    async def test_shared_daily_limit_survives_topics_and_restart(self):
        for actor_id in (1, 2):
            await self.coordinator.observe(message("С днем рождения!", from_user=SimpleNamespace(id=actor_id, is_bot=False)), timestamp(hour=9))
        await self.storage.close()
        self.storage = SQLiteEntertainmentStorage(self.path)
        await self.storage.initialize()
        self.coordinator = BirthdayCoordinator(self.bot, self.storage, {CHAT}, [])
        for actor_id in (3, 4, 5):
            await self.coordinator.observe(message("С днем рождения!", message_thread_id=99,
                from_user=SimpleNamespace(id=actor_id, is_bot=False),
                date=datetime.fromtimestamp(timestamp(hour=15), timezone.utc)), timestamp(hour=15))
        self.assertEqual(self.bot.send_message.await_count, 1)

    async def test_new_moscow_day_has_a_new_slot(self):
        # Both instants are on the same UTC date, but different Moscow dates.
        for day, hour, minute in ((3, 23, 59), (4, 0, 1)):
            for actor_id in (1, 2):
                await self.coordinator.observe(message("С днем рождения!",
                    from_user=SimpleNamespace(id=actor_id, is_bot=False),
                    date=datetime.fromtimestamp(timestamp(day=day, hour=hour, minute=minute), timezone.utc)),
                    timestamp(day=day, hour=hour, minute=minute))
        self.assertEqual(self.bot.send_message.await_count, 2)

    async def test_old_messages_bots_and_nonbirthday_congratulations_do_not_consume_daily_slot(self):
        for item in (message("Поздравляю с победой!"), message("С днем рождения!", from_user=SimpleNamespace(id=1, is_bot=True)),
                     message("С днем рождения!", forward_origin=SimpleNamespace()),
                     message("С днем рождения!", date=datetime.fromtimestamp(timestamp(day=2, hour=23), timezone.utc))):
            await self.coordinator.observe(item, timestamp(hour=9))
        self.assertEqual(self.bot.send_message.await_count, 0)
        await self.coordinator.observe(message("С днем рождения!"), timestamp(hour=9))
        self.assertEqual(self.bot.send_message.await_count, 1)

    async def test_generic_party_reserves_planned_day_for_the_approved_recipient(self):
        self.coordinator = BirthdayCoordinator(self.bot, self.storage, {CHAT}, [plan()])
        for actor_id in (1, 2, 3):
            await self.coordinator.observe(message("@SomeoneElse, с днем рождения!",
                from_user=SimpleNamespace(id=actor_id, is_bot=False)), timestamp(hour=9))
        self.assertEqual(self.bot.send_message.await_count, 0)
        await self.coordinator.tick(timestamp())
        self.assertEqual(self.bot.send_message.await_count, 1)
        for actor_id in (4, 5):
            await self.coordinator.observe(message("С днем рождения!", from_user=SimpleNamespace(id=actor_id, is_bot=False)), timestamp(hour=13))
        self.assertEqual(self.bot.send_message.await_count, 1)

    async def test_reactive_messages_cannot_emit_legacy_hourly_congratulations(self):
        service = EntertainmentService(SimpleNamespace(bot=self.bot), self.storage, {CHAT},
                                       now_fn=lambda: timestamp(hour=9), birthday_plans=[])
        service.evaluate_topic = AsyncMock(side_effect=AssertionError("birthday message must be handled by daily coordinator"))
        for actor_id in (1, 2, 3, 4):
            await service.observe_message(message("С днем рождения!", from_user=SimpleNamespace(id=actor_id, is_bot=False)))
        self.assertEqual(self.bot.send_message.await_count, 1)

    def test_authored_variations_are_readable_and_vary_across_the_year(self):
        texts = [birthday_module.birthday_greeting(CHAT, date(2026, 1, 1) + timedelta(days=i)) for i in range(365)]
        self.assertGreater(len(set(texts)), 250)
        for text in texts:
            self.assertIn("\n\n", text)
            self.assertLess(len(text), 3500)
            self.assertNotRegex(text, r"[А-Яа-я][🦝🎂💛🌷✨💖🎁][А-Яа-я]")


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL is not configured")
class BirthdayPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def test_competing_claims_and_restart_are_durable(self):
        storage = PostgresEntertainmentStorage(os.environ["TEST_DATABASE_URL"])
        await storage.initialize()
        prefix = "birthday-postgres-test"
        try:
            await storage._require_pool().execute("DELETE FROM entertainment_birthday_delivery WHERE chat_id=$1 AND event_id LIKE $2", CHAT, prefix + "%")
            claims = await asyncio.gather(*[storage.claim_birthday_delivery(CHAT, f"{prefix}-{i}", timestamp()) for i in range(6)])
            self.assertEqual(claims.count(True), 1)
            winner = f"{prefix}-{claims.index(True)}"
            await storage.finish_birthday_delivery(CHAT, winner, timestamp(), "SENT", 123)
            await storage.close()
            await storage.initialize()
            self.assertFalse(await storage.claim_birthday_delivery(CHAT, prefix + "-restart", timestamp()))
            self.assertTrue(await storage.claim_birthday_delivery(CHAT, prefix + "-tomorrow", timestamp(day=4)))
        finally:
            await storage._require_pool().execute("DELETE FROM entertainment_birthday_delivery WHERE chat_id=$1 AND event_id LIKE $2", CHAT, prefix + "%")
            await storage.close()

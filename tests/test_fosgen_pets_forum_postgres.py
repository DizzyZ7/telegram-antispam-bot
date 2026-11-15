"""Real dispatcher + PostgreSQL across forum topics and Telegram timestamps."""
from datetime import datetime, timedelta, timezone
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import uuid

import asyncpg
from aiogram import Bot, Dispatcher, F
from aiogram.types import Chat, Message, Update, User

from fosgen_pets.postgres import PostgresPetStore
from fosgen_pets.core import MessageEligibility
from fosgen_pets.telegram import register_pet_handlers
from tests.test_fosgen_pets_telegram import RecordingSession

URL = os.getenv("TEST_DATABASE_URL", "").strip()
GROUP, OWNER = -100123, 11


@unittest.skipUnless(URL, "Disposable TEST_DATABASE_URL required")
class ForumCountingPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.schema = "pet_forum_" + uuid.uuid4().hex
        self.admin = await asyncpg.connect(URL)
        await self.admin.execute(f'CREATE SCHEMA "{self.schema}"')
        parts = urlsplit(URL)
        query = dict(parse_qsl(parts.query))
        query["search_path"] = self.schema
        self.store = PostgresPetStore(urlunsplit(parts._replace(query=urlencode(query))))
        await self.store.initialize()
        self.bot = Bot("777:" + "a" * 35, session=RecordingSession())
        self.app = SimpleNamespace(dp=Dispatcher(), ALLOWED_CHATS=[GROUP], pending_users={})
        self.collected = []
        async def collect_text_messages(message):
            self.collected.append(message.message_id)
        self.app.collect_text_messages = collect_text_messages
        self.app.dp.message.register(collect_text_messages, F.text)
        register_pet_handlers(self.app, self.store, chat_ids=frozenset({GROUP}))
        self.base = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=2)
        self.update_id = 0

    async def asyncTearDown(self):
        await self.bot.session.close()
        await self.store.close()
        await self.admin.execute(f'DROP SCHEMA "{self.schema}" CASCADE')
        await self.admin.close()

    async def dispatch(self, text, *, chat=GROUP, thread=1234, seconds=0):
        self.update_id += 1
        fields = {"is_topic_message": True, "message_thread_id": thread} if chat < 0 else {}
        message = Message(message_id=self.update_id, date=self.base + timedelta(seconds=seconds),
                          chat=Chat(id=chat, type="supergroup" if chat < 0 else "private", is_forum=chat < 0),
                          from_user=User(id=OWNER, is_bot=False, first_name="User"), text=text, **fields)
        await self.app.dp.feed_update(self.bot, Update(update_id=self.update_id, message=message))

    async def test_all_forum_topics_feed_the_same_owner_progress(self):
        await self.store.opt_in(OWNER, now=self.base - timedelta(seconds=1))
        for index, thread in enumerate((1234, 5678, 9012)):
            await self.dispatch(f"Разговор в теме номер {thread}", thread=thread, seconds=index * 31)
        self.assertEqual(self.collected, [1, 2, 3])
        self.assertEqual((await self.store.progress(OWNER)).count, 3)

    async def test_switching_topic_does_not_bypass_owner_cooldown(self):
        await self.store.opt_in(OWNER, now=self.base - timedelta(seconds=1))
        await self.dispatch("Первое обычное сообщение", thread=1234, seconds=0)
        await self.dispatch("Второе обычное сообщение", thread=5678, seconds=1)
        self.assertEqual((await self.store.progress(OWNER)).count, 1)
        await self.dispatch("Третье обычное сообщение", thread=5678, seconds=30)
        self.assertEqual((await self.store.progress(OWNER)).count, 2)

    async def test_telegram_consent_and_same_second_forum_message_use_one_clock(self):
        # Bot clock can have finer precision or drift relative to Telegram.
        # The consent and group events both carry Telegram's second-resolution clock.
        with patch("fosgen_pets.postgres.datetime") as clock:
            clock.now.return_value = self.base + timedelta(minutes=5, microseconds=500000)
            await self.dispatch("/pet_start", chat=OWNER)
        await self.dispatch("Сегодня проверяю питомца в теме", thread=5678)
        self.assertEqual((await self.store.progress(OWNER)).count, 1)
        async with self.store._pool.acquire() as conn:
            cutoff = await conn.fetchval("SELECT opted_in_at FROM fosgen_pet_opt_ins WHERE user_id=$1", OWNER)
        self.assertEqual(cutoff, self.base)

    async def test_database_decision_reasons_identify_counter_rejections(self):
        def message(ident, text="Проверка первого сообщения"):
            return MessageEligibility(OWNER, GROUP, ident, text, opted_in=True)
        missing = await self.store.record_message(message(1), now=self.base)
        self.assertEqual(missing.reason, "consent_missing")
        await self.store.opt_in(OWNER, now=self.base)
        early = await self.store.record_message(message(1), now=self.base - timedelta(seconds=1))
        self.assertEqual(early.reason, "before_consent")
        first = await self.store.record_message(message(1), now=self.base)
        self.assertEqual((first.reason, first.count), ("counted", 1))
        fast = await self.store.record_message(message(2, "Другое подходящее сообщение"), now=self.base + timedelta(seconds=1))
        self.assertEqual(fast.reason, "cooldown")
        duplicate = await self.store.record_message(message(3), now=self.base + timedelta(seconds=31))
        self.assertEqual(duplicate.reason, "repeated_text")
        replay = await self.store.record_message(message(1), now=self.base + timedelta(seconds=31))
        self.assertEqual(replay.reason, "replayed_update")
        await self.store.revoke(OWNER)
        paused = await self.store.record_message(message(4), now=self.base + timedelta(seconds=61))
        self.assertEqual(paused.reason, "consent_paused")

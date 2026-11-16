"""Timed outings use isolated real PostgreSQL schemas, never production tables."""
import asyncio
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import os
import unittest

from tests import test_fosgen_pets_postgres as fixtures

START = fixtures.START

URL = os.getenv("TEST_DATABASE_URL", "").strip()


@unittest.skipUnless(URL, "Disposable PostgreSQL required")
class WalkPostgresTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.PostgresPetStoreTests.asyncSetUp
    asyncTearDown = fixtures.PostgresPetStoreTests.asyncTearDown
    ready = fixtures.PostgresPetStoreTests.ready
    adopt = fixtures.PostgresPetStoreTests.adopt

    async def walks(self, owner=11):
        from fosgen_pets.walks_postgres import PetWalkStore
        await self.ready(owner)
        pet = await self.adopt(owner)
        return PetWalkStore(self.store), pet

    async def test_walk_requires_a_permanent_pet_and_does_not_adopt_one(self):
        from fosgen_pets.walks_postgres import PetWalkStore
        walks = PetWalkStore(self.store)
        result = await walks.start(11, -100, 1, "meadow", now=START)
        self.assertEqual(result.reason, "pet_missing")
        self.assertIsNone(result.walk)
        async with self.store._pool.acquire() as conn:
            self.assertEqual(await conn.fetchval("SELECT count(*) FROM fosgen_pet_walks"), 0)

    async def test_completion_gives_twenty_xp_without_a_worker_or_claim_deadline(self):
        walks, pet = await self.walks()
        result = await walks.start(11, -100, 1, "meadow", thread_id=1234, now=START)
        self.assertEqual(result.reason, "started")
        self.assertEqual(result.walk.ends_at, START + timedelta(minutes=30))
        self.assertEqual((await walks.growth(11, now=START + timedelta(minutes=29))).xp, 0)
        growth = await walks.growth(11, now=START + timedelta(minutes=30))
        self.assertEqual((growth.xp, growth.completed_walks, growth.level), (20, 1, 1))
        for _ in range(3):
            self.assertEqual((await walks.growth(11, now=START + timedelta(days=100))).xp, 20)
        restored = await self.store.get(11)
        self.assertEqual((restored.id, restored.png, restored.svg, restored.origin_story),
                         (pet.id, pet.png, pet.svg, pet.origin_story))

    async def test_owner_daily_cap_is_global_across_groups_topics_and_requests(self):
        walks, _ = await self.walks()
        await walks.start(11, -100, 1, "forest", thread_id=1, now=START)
        active = await walks.start(11, -200, 2, "meadow", thread_id=2, now=START + timedelta(minutes=1))
        self.assertEqual(active.reason, "active")
        same_day = await walks.start(11, -200, 3, "river", now=START + timedelta(hours=3))
        self.assertEqual(same_day.reason, "daily_limit")
        other_day = await walks.start(11, -200, 4, "river", now=START + timedelta(days=1))
        self.assertEqual(other_day.reason, "started")
        self.assertEqual((await walks.growth(11, now=START + timedelta(days=1, hours=1))).xp, 40)

    async def test_parallel_start_and_replayed_update_have_exactly_one_reward(self):
        walks, _ = await self.walks()
        results = await asyncio.gather(*(walks.start(11, -100, n, "forest", now=START) for n in range(1, 9)))
        self.assertEqual(sum(r.reason == "started" for r in results), 1)
        original = next(r.walk for r in results if r.reason == "started")
        replay = await walks.start(11, -100, original.message_id, "forest", now=START + timedelta(days=1))
        self.assertEqual(replay.reason, "replay")
        self.assertEqual(replay.walk.id, original.id)
        self.assertEqual((await walks.growth(11, now=START + timedelta(days=1))).xp, 20)
        async with self.store._pool.acquire() as conn:
            self.assertEqual(await conn.fetchval("SELECT count(*) FROM fosgen_pet_walks"), 1)

    async def test_midnight_does_not_allow_two_simultaneous_walks(self):
        walks, _ = await self.walks()
        late = START.replace(hour=20, minute=55)  # 23:55 Moscow
        await walks.start(11, -100, 1, "meadow", now=late)
        active = await walks.start(11, -100, 2, "forest", now=late + timedelta(minutes=10))
        self.assertEqual(active.reason, "active")
        next_day = await walks.start(11, -100, 3, "forest", now=late + timedelta(minutes=30))
        self.assertEqual(next_day.reason, "started")
        self.assertEqual(next_day.walk.day, date(2026, 1, 2))

    async def test_diary_is_scoped_paged_and_keeps_original_topics_and_stories(self):
        walks, _ = await self.walks()
        for owner in range(11, 23):
            await self.ready(owner)
            await self.adopt(owner)
            await walks.start(owner, -100 if owner < 22 else -200, owner, "forest", thread_id=owner, now=START)
        first = await walks.diary(-100, START.date(), page=1, now=START + timedelta(hours=1))
        second = await walks.diary(-100, START.date(), page=2, now=START + timedelta(hours=1))
        self.assertEqual((first.total, len(first.walks), len(second.walks)), (11, 10, 1))
        self.assertTrue(all(w.chat_id == -100 and w.thread_id != 22 for w in first.walks + second.walks))
        self.assertEqual(first.walks[0].thread_id, 11)
        other = await walks.diary(-999, START.date(), now=START)
        self.assertEqual(other.total, 0)
        from unittest.mock import patch
        with patch("fosgen_pets.walks_postgres.activity", side_effect=AssertionError("Never regenerate old stories")):
            again = await walks.diary(-100, START.date(), now=START + timedelta(days=1))
        self.assertEqual(again.walks[0].activity, first.walks[0].activity)

    async def test_restart_and_repeat_migration_preserve_walks_and_growth(self):
        walks, pet = await self.walks()
        result = await walks.start(11, -100, 1, "river", now=START)
        await self.store.close()
        from fosgen_pets.postgres import PostgresPetStore
        from fosgen_pets.walks_postgres import PetWalkStore
        self.store = PostgresPetStore(self.dsn)
        await self.store.initialize()
        walks = PetWalkStore(self.store)
        restored = await walks.diary(-100, START.date(), now=START + timedelta(days=90))
        self.assertEqual(restored.walks[0], result.walk)
        self.assertEqual((await walks.growth(11, now=START + timedelta(days=90))).xp, 20)
        self.assertEqual((await self.store.get(11)).png, pet.png)

    async def test_invalid_inputs_are_rejected_without_storing_metadata(self):
        walks, _ = await self.walks()
        for owner, chat, msg, route, thread in ((0,-100,1,"forest",None), (True,-100,1,"forest",None),
                (11,100,1,"forest",None), (11,-100,0,"forest",None),
                (11,-100,1,"private-raw-text",None), (11,-100,1,"forest",-1)):
            with self.subTest(owner=owner, chat=chat, msg=msg, route=route, thread=thread), self.assertRaises(ValueError):
                await walks.start(owner, chat, msg, route, thread_id=thread, now=START)
        for page in (0, True, 100001, "1"):
            with self.assertRaises(ValueError):
                await walks.diary(-100, START.date(), page=page)

    async def test_real_dispatcher_and_pg_walk_in_isolated_forum_then_private_xp(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from aiogram import Bot, Dispatcher, F
        from aiogram.methods import SendMessage, SendPhoto
        from aiogram.types import Chat, Message, Update, User
        from fosgen_pets.runtime import start_pet_runtime
        from lexicon_game_scope import register_lexicon_only_guard
        from tests.test_fosgen_pets_lexicon_scope import LexiconSession
        walks, pet = await self.walks()
        app = SimpleNamespace(dp=Dispatcher(), ALLOWED_CHATS=[], pending_users={})
        legacy = []
        async def collect_text_messages(message):
            legacy.append(message.message_id)
        app.collect_text_messages = collect_text_messages
        app.dp.message.register(collect_text_messages, F.text)
        scope = register_lexicon_only_guard(app, {-100})
        bot = Bot("777:" + "a" * 35, session=LexiconSession())
        self.addAsyncCleanup(bot.session.close)
        with patch("fosgen_pets.runtime.PostgresPetStore", return_value=self.store):
            runtime = await start_pet_runtime(app, database_url=self.dsn, isolated_scope=scope,
                environ={"FOSGEN_PETS_ENABLED": "1", "FOSGEN_PETS_CHAT_IDS": "-100",
                         "FOSGEN_PETS_SOCIAL_ENABLED": "1"})
        self.assertIsNotNone(runtime)
        now = datetime.now(timezone.utc)
        event = Message(message_id=1, date=now, chat=Chat(id=-100, type="supergroup", is_forum=True),
            message_thread_id=777, is_topic_message=True,
            from_user=User(id=11, is_bot=False, first_name="User"), text="/pet_graze")
        await app.dp.feed_update(bot, Update(update_id=1, message=event))
        reply = next(r for r in bot.session.requests if isinstance(r, SendMessage))
        self.assertIn("на прогулке", reply.text)
        self.assertEqual(reply.message_thread_id, 777)
        self.assertEqual(legacy, [])
        diary = await walks.diary(-100, now.astimezone(ZoneInfo("Europe/Moscow")).date())
        self.assertEqual(diary.total, 1)
        # Simulate a returning owner after a long server outage: no worker lost a timer.
        growth = await walks.growth(11, now=now + timedelta(hours=1))
        self.assertEqual(growth.xp, 20)
        self.assertEqual((await self.store.get(11)).png, pet.png)

    async def test_a_conflicting_telegram_message_never_leaks_another_owners_walk(self):
        walks, first = await self.walks()
        await self.ready(12)
        await self.adopt(12)
        await walks.start(11, -100, 1, "forest", now=START)
        result = await walks.start(12, -100, 1, "forest", now=START)
        self.assertEqual(result.reason, "request_conflict")
        self.assertIsNone(result.walk)
        self.assertEqual((await walks.growth(12, now=START + timedelta(days=1))).xp, 0)

    async def test_database_rejects_a_walk_for_a_pet_owned_by_someone_else(self):
        import asyncpg
        walks, pet = await self.walks()
        await self.ready(12)
        async with self.store._pool.acquire() as conn:
            with self.assertRaises(asyncpg.ForeignKeyViolationError):
                await conn.execute("""INSERT INTO fosgen_pet_walks
                    (pet_id,owner_user_id,chat_id,message_id,walk_day,route,activity,started_at,ends_at)
                    VALUES($1,12,-100,1,$2,'forest','Подмена владельца',$3,$4)""",
                    pet.id, START.date(), START, START + timedelta(minutes=30))

    async def test_rejected_daily_limit_cannot_start_a_walk_when_replayed_after_midnight(self):
        walks, _ = await self.walks()
        await walks.start(11, -100, 1, "forest", now=START)
        rejected_at = START.replace(hour=20, minute=59)
        rejected = await walks.start(11, -100, 2, "forest", now=rejected_at)
        self.assertEqual(rejected.reason, "daily_limit")
        from fosgen_pets.postgres import PostgresPetStore
        from fosgen_pets.walks_postgres import PetWalkStore
        await self.store.close()
        self.store = PostgresPetStore(self.dsn)
        walks = PetWalkStore(self.store)
        repeats = await asyncio.gather(*(walks.start(11, -100, 2, "forest",
            now=rejected_at + timedelta(minutes=2)) for _ in range(4)))
        self.assertTrue(all(r.reason == "replay" for r in repeats))
        self.assertEqual((await walks.growth(11, now=START + timedelta(days=2))).xp, 20)
        fresh = await walks.start(11, -100, 3, "forest", now=rejected_at + timedelta(minutes=3))
        self.assertEqual(fresh.reason, "started")

    async def test_rejected_active_request_remains_used_after_walk_finishes(self):
        walks, _ = await self.walks()
        late = START.replace(hour=20, minute=32)  # completes 00:02 Moscow
        await walks.start(11, -100, 1, "meadow", now=late)
        rejected = await walks.start(11, -100, 2, "forest", now=late + timedelta(minutes=27))
        self.assertEqual(rejected.reason, "active")
        repeated = await walks.start(11, -100, 2, "forest", now=late + timedelta(minutes=31))
        self.assertEqual(repeated.reason, "replay")
        # Even older, out-of-order updates are not treated as fresh intentions.
        older = await walks.start(11, -100, 1, "forest", now=late + timedelta(minutes=32))
        self.assertEqual(older.reason, "replay")

    async def test_rejected_requests_use_one_watermark_per_chat_not_an_event_log(self):
        walks, _ = await self.walks()
        await walks.start(11, -100, 1, "meadow", now=START)
        for number in range(2, 302):
            await walks.start(11, -100, number, "forest", now=START + timedelta(minutes=1))
        async with self.store._pool.acquire() as conn:
            count = await conn.fetchval("SELECT COUNT(*) FROM fosgen_pet_walk_request_watermarks WHERE owner_user_id=11")
            last = await conn.fetchval("SELECT last_message_id FROM fosgen_pet_walk_request_watermarks WHERE owner_user_id=11 AND chat_id=-100")
        self.assertEqual((count, last), (1, 301))
        self.assertEqual((await walks.growth(11, now=START + timedelta(hours=1))).xp, 20)

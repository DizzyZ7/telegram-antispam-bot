"""Social commands go through a real dispatcher and existing moderation."""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from aiogram import Bot, Dispatcher, F
from aiogram.methods import SendMessage
from aiogram.types import Chat, Message, Update, User

from tests.test_fosgen_pets_telegram import BoundaryStore, RecordingSession, pet_fixture
from tests.test_fosgen_pets_lexicon_scope import LexiconSession
from lexicon_game_scope import register_lexicon_only_guard, register_lexicon_silent_profanity_filter
from fosgen_pets.telegram import register_pet_handlers, _status
from fosgen_pets.walks_postgres import Walk, WalkDiary, WalkResult, Growth

GROUP, OTHER, OWNER = -1002659916114, -100999, 11
NOW = datetime.now(timezone.utc).replace(microsecond=0)


def walk_fixture():
    return Walk(1, 9, "calf", GROUP, 1, 123, NOW.date(), "meadow", "Щипал свежую травку.", NOW,
                NOW + timedelta(minutes=30))


class WalkDispatcherTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from fosgen_pets.walks_telegram import register_walk_handlers
        clock = patch("fosgen_pets.walks_telegram.datetime")
        clock.start().now.return_value = NOW
        self.addCleanup(clock.stop)
        self.session = LexiconSession()
        self.bot = Bot("777:" + "a" * 35, session=self.session)
        self.addAsyncCleanup(self.bot.session.close)
        self.app = SimpleNamespace(dp=Dispatcher(), ALLOWED_CHATS=[], pending_users={})
        self.legacy = []
        async def collect_text_messages(message):
            self.legacy.append(message.message_id)
        self.app.collect_text_messages = collect_text_messages
        self.app.dp.message.register(collect_text_messages, F.text)
        register_lexicon_silent_profanity_filter(self.app, {GROUP})
        register_lexicon_only_guard(self.app, {GROUP})
        self.walks = SimpleNamespace(start=AsyncMock(return_value=WalkResult(walk_fixture(), "started")),
            diary=AsyncMock(return_value=WalkDiary(NOW.date())), growth=AsyncMock(return_value=Growth(100, 5)))
        self.service = register_walk_handlers(self.app, self.walks, chat_ids=frozenset({GROUP}),
                                             isolated_chat_ids=frozenset({GROUP}))
        self.counter = 0

    async def dispatch(self, text, *, chat=GROUP, owner=OWNER, **fields):
        self.counter += 1
        event = Message(message_id=self.counter, date=fields.pop("date", NOW),
            chat=Chat(id=chat, type="supergroup" if chat < 0 else "private", is_forum=chat < 0),
            from_user=User(id=owner, is_bot=False, first_name="User"), text=text, **fields)
        await self.app.dp.feed_update(self.bot, Update(update_id=self.counter, message=event))

    def replies(self):
        return [r for r in self.session.requests if isinstance(r, SendMessage)]

    async def test_isolated_forum_walk_keeps_topic_and_global_allowlist_closed(self):
        await self.dispatch("/pet_graze@FosgenBot", message_thread_id=123, is_topic_message=True)
        self.walks.start.assert_awaited_once()
        args, kwargs = self.walks.start.await_args
        self.assertEqual(args, (OWNER, GROUP, 1, "meadow"))
        self.assertEqual(kwargs["thread_id"], 123)
        self.assertEqual(self.app.ALLOWED_CHATS, [])
        self.assertEqual(self.legacy, [])
        self.assertEqual(self.replies()[0].message_thread_id, 123)
        self.assertIn("на прогулке", self.replies()[0].text)
        self.assertNotIn("DNA", self.replies()[0].text)
        self.assertNotIn("passport", self.replies()[0].text)

    async def test_foreign_groups_and_nonoriginal_senders_cannot_start_walks(self):
        from aiogram.types import MessageOriginUser
        await self.dispatch("/pet_walk", chat=OTHER)
        await self.dispatch("/pet_walk", sender_chat=Chat(id=GROUP, type="supergroup"))
        await self.dispatch("/pet_walk", edit_date=int(NOW.timestamp()))
        await self.dispatch("/pet_walk", via_bot=User(id=7, is_bot=True, first_name="Bot"))
        await self.dispatch("/pet_walk", forward_origin=MessageOriginUser(date=NOW,
            sender_user=User(id=8, is_bot=False, first_name="Other")))
        self.walks.start.assert_not_awaited()
        self.assertEqual(self.replies(), [])

    async def test_stale_commands_and_captcha_are_ignored(self):
        await self.dispatch("/pet_walk", date=NOW - timedelta(minutes=6))
        await self.dispatch("/pet_walk", date=NOW + timedelta(minutes=2))
        self.app.pending_users[OWNER] = True
        await self.dispatch("/pet_walk")
        self.walks.start.assert_not_awaited()

    async def test_private_command_explains_publication_without_starting_a_walk(self):
        await self.dispatch("/pet_walk", chat=OWNER)
        self.walks.start.assert_not_awaited()
        self.assertIn("в подключенном чате", self.replies()[0].text.casefold())

    async def test_invalid_routes_and_diary_arguments_do_not_reach_storage(self):
        await self.dispatch("/pet_walk private raw text")
        await self.dispatch("/pet_diary -100123", owner=12)
        await self.dispatch("/pet_diary 2026-11-16 0", owner=13)
        self.walks.start.assert_not_awaited()
        self.walks.diary.assert_not_awaited()
        self.assertEqual(len(self.replies()), 3)

    async def test_diary_is_current_group_only_bounded_and_uses_saved_text(self):
        row = walk_fixture()
        from dataclasses import replace
        self.walks.diary.return_value = WalkDiary(date(2026, 11, 16),
            tuple(replace(row, id=n, pet_id=n, activity="А" * 250,
                ends_at=NOW - timedelta(seconds=1)) for n in range(10)), total=111, page=1)
        await self.dispatch("/pet_diary 2026-11-16 1", message_thread_id=888, is_topic_message=True)
        self.walks.diary.assert_awaited_once_with(GROUP, date(2026, 11, 16), page=1)
        text = self.replies()[0].text
        self.assertLessEqual(len(text), 4096)
        self.assertIn("/pet_diary 2026-11-16 2", text)
        self.assertNotIn("tg://", text)
        self.assertEqual(self.replies()[0].message_thread_id, 888)

    async def test_repeated_commands_have_bounded_replies_and_no_extra_storage_calls(self):
        for _ in range(20):
            await self.dispatch("/pet_walk")
        self.walks.start.assert_awaited_once()
        self.assertEqual(len(self.replies()), 1)

    async def test_profanity_is_removed_before_the_social_handler(self):
        await self.dispatch("/pet_walk блять")
        self.walks.start.assert_not_awaited()
        self.assertEqual(self.legacy, [])

    async def test_database_failure_is_contained_and_logs_no_raw_details(self):
        self.walks.start.side_effect = RuntimeError("secret private DSN")
        with self.assertLogs("fosgen_pets.walks_telegram", "WARNING") as logs:
            await self.dispatch("/pet_walk")
        self.assertNotIn("secret", " ".join(logs.output))
        self.assertIn("сохраняются", self.replies()[0].text)
        self.assertEqual(self.legacy, [])


    async def test_storage_value_error_is_not_misreported_as_bad_command_syntax(self):
        self.walks.start.side_effect = ValueError("private broken row")
        with self.assertLogs("fosgen_pets.walks_telegram", "WARNING") as logs:
            await self.dispatch("/pet_walk")
        self.assertIn("сохраняются", self.replies()[0].text)
        self.assertNotIn("private", " ".join(logs.output))


class BiographyTelegramTests(unittest.IsolatedAsyncioTestCase):
    async def test_card_and_passport_show_species_and_sex_without_changing_art(self):
        pet = pet_fixture()
        text = _status(pet)
        self.assertIn("Вид: котенок", text)
        self.assertIn("Пол:", text)
        session = RecordingSession()
        bot = Bot("777:" + "a" * 35, session=session)
        self.addAsyncCleanup(bot.session.close)
        store = BoundaryStore()
        store.pet = pet
        app = SimpleNamespace(dp=Dispatcher(), ALLOWED_CHATS=[], pending_users={})
        register_pet_handlers(app, store, chat_ids=frozenset())
        event = Message(message_id=1, date=NOW, chat=Chat(id=OWNER, type="private"),
                        from_user=User(id=OWNER, first_name="User", is_bot=False), text="/pet_passport")
        await app.dp.feed_update(bot, Update(update_id=1, message=event))
        reply = next(r for r in session.requests if isinstance(r, SendMessage))
        self.assertIn("Пол:", reply.text)
        self.assertIn(pet.appearance.fingerprint(), reply.text)
        self.assertIn(pet.appearance.canonical_dna().replace('"','&quot;'), reply.text)

    async def test_social_config_is_explicit_and_rejects_malformed_flags(self):
        from fosgen_pets.runtime import PetsConfig
        env = {"FOSGEN_PETS_ENABLED": "1", "FOSGEN_PETS_CHAT_IDS": str(GROUP)}
        self.assertFalse(PetsConfig.from_env(env).social_enabled)
        self.assertTrue(PetsConfig.from_env(dict(env, FOSGEN_PETS_SOCIAL_ENABLED="1")).social_enabled)
        with self.assertRaises(ValueError):
            PetsConfig.from_env(dict(env, FOSGEN_PETS_SOCIAL_ENABLED="yes"))

    async def test_growth_is_visible_and_a_broken_growth_read_keeps_the_saved_photo(self):
        from aiogram.methods import SendPhoto
        store = BoundaryStore()
        store.pet = pet_fixture()
        session = RecordingSession()
        bot = Bot("777:" + "a" * 35, session=session)
        self.addAsyncCleanup(bot.session.close)
        app = SimpleNamespace(dp=Dispatcher(), ALLOWED_CHATS=[], pending_users={})
        service = register_pet_handlers(app, store, chat_ids=frozenset())
        service.walks = SimpleNamespace(growth=AsyncMock(return_value=Growth(100, 5)))
        event = Message(message_id=1, date=NOW, chat=Chat(id=OWNER, type="private"),
                        from_user=User(id=OWNER, first_name="User", is_bot=False), text="/pet")
        await app.dp.feed_update(bot, Update(update_id=1, message=event))
        photo = next(r for r in session.requests if isinstance(r, SendPhoto))
        self.assertIn("Уровень дружбы: 3", photo.caption)
        self.assertIn("Опыт: 100", photo.caption)
        self.assertEqual(photo.photo.data, store.pet.png)
        service.walks.growth.side_effect = ValueError("private metadata")
        with self.assertLogs("fosgen_pets.telegram", "WARNING") as logs:
            await app.dp.feed_update(bot, Update(update_id=2, message=event))
        photos = [r for r in session.requests if isinstance(r, SendPhoto)]
        self.assertEqual(len(photos), 2)
        self.assertNotIn("private", " ".join(logs.output))
        self.assertEqual(photos[-1].photo.data, store.pet.png)


class ReplyGateTests(unittest.TestCase):
    def test_reply_gate_is_bounded_and_allows_the_next_request_after_thirty_seconds(self):
        from fosgen_pets.walks_telegram import ReplyGate
        gate = ReplyGate()
        with patch("fosgen_pets.walks_telegram.time.monotonic", return_value=100):
            self.assertTrue(gate.allow(11, "walk"))
            self.assertFalse(gate.allow(11, "walk"))
            self.assertTrue(gate.allow(11, "diary"))
        with patch("fosgen_pets.walks_telegram.time.monotonic", return_value=130):
            self.assertTrue(gate.allow(11, "walk"))
            for user in range(5000):
                gate.allow(user, "walk")
        self.assertLessEqual(len(gate._last), 4096)

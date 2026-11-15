"""Explicit Pets scope in a Lexicon-only chat never widens legacy permissions."""
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from aiogram import Bot, Dispatcher, F
from aiogram.methods import DeleteMessage, SendMessage
from aiogram.types import Chat, Message, Update, User

from fosgen_pets.runtime import start_pet_runtime
from lexicon_game_scope import (enforce_lexicon_only_isolation, register_lexicon_only_guard,
                                register_lexicon_silent_profanity_filter)
from minigames import MiniGameGuessMiddleware
from tests.test_fosgen_pets_telegram import BoundaryStore, RecordingSession

NORMAL, ISOLATED, OWNER = -1002619489118, -1002659916114, 123


class LexiconSession(RecordingSession):
    async def make_request(self, bot, method, timeout=None):
        if isinstance(method, DeleteMessage):
            self.requests.append(method)
            return True
        return await super().make_request(bot, method, timeout)


class IsolatedPetsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = BoundaryStore()
        self.store.initialize = AsyncMock()
        self.store.close = AsyncMock()
        self.store.opted_in = True
        self.app = SimpleNamespace(dp=Dispatcher(), ALLOWED_CHATS=[NORMAL, ISOLATED], pending_users={})
        enforce_lexicon_only_isolation(self.app, {ISOLATED})
        self.legacy_calls = []
        async def collect_text_messages(message):
            self.legacy_calls.append(message.message_id)
        self.app.collect_text_messages = collect_text_messages
        self.app.dp.message.register(collect_text_messages, F.text)
        register_lexicon_silent_profanity_filter(self.app, {ISOLATED})
        self.guessing = SimpleNamespace(handle_word_guess=AsyncMock())
        self.app.dp.message.outer_middleware(MiniGameGuessMiddleware(self.guessing))
        self.scope = register_lexicon_only_guard(self.app, {ISOLATED})
        self.session = LexiconSession()
        self.bot = Bot("777:" + "a" * 35, session=self.session)
        self.addAsyncCleanup(self.bot.session.close)
        self.counter = 0

    async def start(self, selected=None):
        self.assertIsNotNone(self.scope, "Lexicon registration must expose its exact terminal guard")
        with patch("fosgen_pets.runtime.PostgresPetStore", return_value=self.store):
            runtime = await start_pet_runtime(self.app, database_url="postgresql://test",
                isolated_scope=self.scope, environ={"FOSGEN_PETS_ENABLED": "1",
                    "FOSGEN_PETS_CHAT_IDS": selected or f"{NORMAL},{ISOLATED}",
                    "FOSGEN_PETTEST_ADMIN_IDS": str(OWNER)})
        if runtime is not None:
            self.addAsyncCleanup(runtime.stop)
        return runtime

    async def dispatch(self, text="Сегодня общаюсь со своим питомцем", *, chat=ISOLATED, thread=None):
        self.counter += 1
        event = Message(message_id=self.counter, date=datetime.now(timezone.utc),
            chat=Chat(id=chat, type="supergroup"), message_thread_id=thread, is_topic_message=thread is not None,
            from_user=User(id=OWNER, is_bot=False, first_name="User"), text=text)
        await self.app.dp.feed_update(self.bot, Update(update_id=self.counter, message=event))

    def records(self):
        return [item[1] for item in self.store.calls if item[0] == "record"]

    async def test_explicit_isolated_chat_counts_after_guard_without_legacy_access(self):
        runtime = await self.start()
        self.assertIsNotNone(runtime)
        await self.dispatch(thread=4321)
        self.assertEqual([m.chat_id for m in self.records()], [ISOLATED])
        self.assertEqual(self.app.ALLOWED_CHATS, [NORMAL])
        self.assertEqual(self.legacy_calls, [])
        self.assertEqual(self.guessing.handle_word_guess.await_count, 1)

    async def test_unselected_lexicon_chat_stays_outside_pets_scope(self):
        runtime = await self.start(str(NORMAL))
        self.assertIsNotNone(runtime)
        await self.dispatch()
        self.assertEqual(self.records(), [])
        self.assertEqual(self.legacy_calls, [])
        self.assertEqual(self.app.ALLOWED_CHATS, [NORMAL])

    async def test_profanity_is_deleted_before_guessing_and_pet_counting(self):
        await self.start()
        await self.dispatch("Это блять проверка счетчика")
        self.assertEqual(len([r for r in self.session.requests if isinstance(r, DeleteMessage)]), 1)
        self.assertEqual(self.records(), [])
        self.assertEqual(self.guessing.handle_word_guess.await_count, 0)
        self.assertEqual(self.legacy_calls, [])

    async def test_foreign_handler_with_guard_name_cannot_count(self):
        await self.start()
        async def lexicon_only_chat_guard(message):
            return None
        self.app.dp.message.register(lexicon_only_chat_guard, F.text)
        self.app.dp.message.handlers.insert(0, self.app.dp.message.handlers.pop())
        await self.dispatch()
        self.assertEqual(self.records(), [])

    async def test_removed_guard_cannot_enable_an_isolated_scope(self):
        self.assertIsNotNone(self.scope, "Need registered guard identity")
        self.app.dp.message.handlers[:] = [h for h in self.app.dp.message.handlers if h.callback is not self.scope.handler]
        with patch("fosgen_pets.runtime.PostgresPetStore") as constructor, self.assertLogs("fosgen_pets.runtime", "WARNING"):
            runtime = await start_pet_runtime(self.app, database_url="postgresql://test",
                isolated_scope=self.scope, environ={"FOSGEN_PETS_ENABLED": "1", "FOSGEN_PETS_CHAT_IDS": str(ISOLATED)})
        self.assertIsNone(runtime)
        constructor.assert_not_called()

    async def test_isolated_only_runtime_needs_no_global_chat_allowance(self):
        self.app.ALLOWED_CHATS.clear()
        runtime = await self.start(str(ISOLATED))
        self.assertIsNotNone(runtime)
        await self.dispatch()
        self.assertEqual(len(self.records()), 1)
        self.assertEqual(self.app.ALLOWED_CHATS, [])
        self.assertEqual(self.legacy_calls, [])

    async def test_missing_dispatcher_fails_closed_before_storage_is_opened(self):
        self.assertIsNotNone(self.scope)
        del self.app.dp
        with patch("fosgen_pets.runtime.PostgresPetStore") as constructor, self.assertLogs("fosgen_pets.runtime", "WARNING"):
            runtime = await start_pet_runtime(self.app, database_url="postgresql://test",
                isolated_scope=self.scope, environ={"FOSGEN_PETS_ENABLED": "1", "FOSGEN_PETS_CHAT_IDS": str(ISOLATED)})
        self.assertIsNone(runtime)
        constructor.assert_not_called()

    async def test_a_guard_without_its_group_filter_cannot_grant_isolated_access(self):
        self.assertIsNotNone(self.scope)
        for record in self.app.dp.message.handlers:
            if record.callback is self.scope.handler:
                record.filters = []
        with patch("fosgen_pets.runtime.PostgresPetStore") as constructor, self.assertLogs("fosgen_pets.runtime", "WARNING"):
            runtime = await start_pet_runtime(self.app, database_url="postgresql://test",
                isolated_scope=self.scope, environ={"FOSGEN_PETS_ENABLED": "1", "FOSGEN_PETS_CHAT_IDS": str(ISOLATED)})
        self.assertIsNone(runtime)
        constructor.assert_not_called()

    async def test_group_commands_remain_consumed_without_consent_or_pet_ui(self):
        await self.start()
        await self.dispatch("/pet_start")
        await self.dispatch("/stats")
        self.assertFalse([r for r in self.session.requests if isinstance(r, SendMessage)])
        self.assertFalse([call for call in self.store.calls if call[0] == "opt_in"])
        self.assertEqual(self.records(), [])
        self.assertEqual(self.legacy_calls, [])

    async def test_private_status_reports_both_selected_groups_as_active(self):
        await self.start()
        message = Message(message_id=99, date=datetime.now(timezone.utc),
            chat=Chat(id=OWNER, type="private"), from_user=User(id=OWNER, is_bot=False, first_name="User"),
            text="/pet_status")
        await self.app.dp.feed_update(self.bot, Update(update_id=99, message=message))
        replies = [r for r in self.session.requests if isinstance(r, SendMessage)]
        self.assertEqual(len(replies), 1)
        self.assertIn(f"Активные группы: {ISOLATED}, {NORMAL}", replies[0].text)
        self.assertIn("Не подключены: нет", replies[0].text)
        self.assertIn("Изолированный учет Pets: 1", replies[0].text)

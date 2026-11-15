"""Private operator diagnostics must not reveal group scope to other users."""
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from aiogram import Bot, Dispatcher, F
from aiogram.methods import SendMessage
from aiogram.types import Chat, Message, Update, User

from fosgen_pets import telegram
from tests.test_fosgen_pets_telegram import BoundaryStore, RecordingSession


class PetStatusTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.session = RecordingSession()
        self.bot = Bot("777:" + "a" * 35, session=self.session)
        self.addAsyncCleanup(self.bot.session.close)
        self.app = SimpleNamespace(dp=Dispatcher())
        self.store = BoundaryStore()
        self.store.opted_in = True
        self.store.count = 12
        self.forwarded = []
        async def legacy(message):
            self.forwarded.append(message.text)
        self.app.dp.message.register(legacy, F.text)

    def register(self):
        register = getattr(telegram, "register_pet_status_handlers", None)
        self.assertTrue(callable(register), "Operators need private active-group diagnostics")
        register(self.app, self.store, admin_ids=frozenset({123}),
                 requested_chat_ids=frozenset({-1001, -1002}), chat_ids=frozenset({-1001}),
                 asset_version=3)

    async def dispatch(self, *, owner=123, chat=123, is_bot=False):
        event = Message(message_id=1, date=datetime.now(timezone.utc),
                        chat=Chat(id=chat, type="private" if chat > 0 else "supergroup"),
                        from_user=User(id=owner, first_name="User", is_bot=is_bot), text="/pet_status")
        await self.app.dp.feed_update(self.bot, Update(update_id=1, message=event))

    async def test_operator_sees_requested_and_effective_groups_with_own_progress(self):
        self.register()
        await self.dispatch()
        replies = [r for r in self.session.requests if isinstance(r, SendMessage)]
        self.assertEqual(len(replies), 1)
        text = replies[0].text
        self.assertIn("v3", text)
        self.assertIn("12/100", text)
        self.assertIn("Активные группы: -1001", text)
        self.assertIn("Не подключены: -1002", text)
        self.assertIn("30 секунд", text)
        self.assertEqual(self.forwarded, [])
        self.assertEqual(self.store.calls, [("progress", 123)])

    async def test_other_private_users_cannot_read_configuration(self):
        self.register()
        await self.dispatch(owner=456, chat=456)
        self.assertFalse(self.session.requests)
        self.assertEqual(self.store.calls, [])
        self.assertEqual(self.forwarded, ["/pet_status"])

    async def test_group_and_cross_user_private_commands_never_expose_scope(self):
        self.register()
        await self.dispatch(chat=-1001)
        await self.dispatch(chat=456)
        await self.dispatch(is_bot=True)
        self.assertFalse(self.session.requests)
        self.assertEqual(self.store.calls, [])
        self.assertEqual(len(self.forwarded), 3)

    async def test_diagnostic_outage_does_not_expose_exception_details(self):
        self.register()
        async def failing_progress(owner):
            raise RuntimeError("postgresql://SECRET:password@database raw text")
        self.store.progress = failing_progress
        with self.assertLogs("fosgen_pets.telegram", "WARNING") as logs:
            await self.dispatch()
        self.assertNotIn("SECRET", " ".join(logs.output))
        self.assertNotIn("raw text", " ".join(logs.output))
        replies = [r for r in self.session.requests if isinstance(r, SendMessage)]
        self.assertEqual(replies[0].text, telegram.UNAVAILABLE)

    async def test_enabled_runtime_registers_status_for_configured_operator(self):
        from fosgen_pets.runtime import start_pet_runtime
        self.app.ALLOWED_CHATS = [-1001]
        self.store.initialize = AsyncMock()
        self.store.close = AsyncMock()
        env = {"FOSGEN_PETS_ENABLED": "1", "FOSGEN_PETS_CHAT_IDS": "-1001,-1002",
               "FOSGEN_PETS_NEW_ASSET_VERSION": "3", "FOSGEN_PETS_V3_ART_APPROVED": "1",
               "FOSGEN_PETTEST_ADMIN_IDS": "123"}
        with patch("fosgen_pets.runtime.PostgresPetStore", return_value=self.store):
            runtime = await start_pet_runtime(self.app, database_url="postgresql://local", environ=env)
        self.assertIsNotNone(runtime)
        await self.dispatch()
        replies = [r for r in self.session.requests if isinstance(r, SendMessage)]
        self.assertIn("Активные группы: -1001", replies[0].text)
        self.assertIn("Не подключены: -1002", replies[0].text)
        self.assertIn("v3", replies[0].text)
        await runtime.stop()

    async def test_bad_diagnostic_admin_setting_cannot_disable_permanent_pets(self):
        from fosgen_pets.runtime import start_pet_runtime
        self.app.ALLOWED_CHATS = [-1001]
        self.store.initialize = AsyncMock()
        self.store.close = AsyncMock()
        env = {"FOSGEN_PETS_ENABLED": "1", "FOSGEN_PETS_CHAT_IDS": "-1001",
               "FOSGEN_PETTEST_ADMIN_IDS": "postgresql://SECRET"}
        with patch("fosgen_pets.runtime.PostgresPetStore", return_value=self.store), \
                self.assertLogs("fosgen_pets.runtime", "WARNING") as logs:
            runtime = await start_pet_runtime(self.app, database_url="postgresql://local", environ=env)
        self.assertIsNotNone(runtime)
        self.assertNotIn("SECRET", " ".join(logs.output))
        await self.dispatch()
        self.assertFalse(self.session.requests)
        await runtime.stop()

"""Operator-only, consent-triggered, bounded message metadata diagnostics."""
from datetime import datetime, timezone
import gc
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import weakref

from aiogram import Bot, Dispatcher, F
from aiogram.methods import SendMessage
from aiogram.types import Chat, Message, Update, User

from fosgen_pets import telegram
from tests.test_fosgen_pets_telegram import BoundaryStore, RecordingSession


class MessageTraceTests(unittest.IsolatedAsyncioTestCase):
    def types(self):
        from importlib import import_module, util
        self.assertIsNotNone(util.find_spec("fosgen_pets.diagnostics"), "Need bounded message-path evidence")
        return import_module("fosgen_pets.diagnostics")

    def message(self, ident=1, *, owner=123, thread=4321, text="PRIVATE RAW MESSAGE"):
        return Message(message_id=ident, date=datetime.now(timezone.utc),
                       chat=Chat(id=-1001, type="supergroup", is_forum=True),
                       message_thread_id=thread, is_topic_message=True,
                       from_user=User(id=owner, first_name="User", is_bot=False), text=text)

    async def test_trace_requires_operator_activation_and_retains_only_latest_metadata(self):
        trace = self.types().PetMessageTrace()
        trace.configure(frozenset({123}))
        trace.begin(self.message())
        self.assertIsNone(trace.get(123))
        trace.enable(456)
        trace.begin(self.message(owner=456))
        self.assertIsNone(trace.get(456))
        trace.enable(123)
        trace.begin(self.message())
        previous = weakref.ref(trace.get(123))
        for ident in range(1, 101):
            trace.begin(self.message(ident))
        latest = trace.get(123)
        self.assertEqual((latest.chat_id, latest.thread_id, latest.message_id), (-1001, 4321, 100))
        self.assertNotIn("PRIVATE RAW", repr(latest))
        gc.collect()
        self.assertIsNone(previous(), "Prior message metadata must not accumulate")
        trace.disable(123)
        self.assertIsNone(trace.get(123))

    async def test_late_completion_cannot_overwrite_newer_message_diagnostics(self):
        trace = self.types().PetMessageTrace()
        trace.configure(frozenset({123}))
        trace.enable(123)
        first, second = self.message(1), self.message(2, thread=6789)
        trace.begin(first)
        trace.begin(second)
        trace.mark(first, "counted")
        self.assertEqual(trace.get(123).reason, "received")
        trace.mark(second, "cooldown")
        self.assertEqual((trace.get(123).message_id, trace.get(123).reason), (2, "cooldown"))

    async def test_outer_moderation_remains_authoritative_and_observable(self):
        module = self.types()
        trace = module.PetMessageTrace()
        trace.configure(frozenset({123}))
        trace.enable(123)
        async def moderation(event, data):
            return "blocked by moderation"
        result = await module.PetDiagnosticIngress(trace)(moderation, self.message(), {})
        self.assertEqual(result, "blocked by moderation")
        self.assertEqual(trace.get(123).reason, "blocked_before_handler")

    async def test_pipeline_exception_preserves_legacy_failure_without_secret_text(self):
        module = self.types()
        trace = module.PetMessageTrace()
        trace.configure(frozenset({123}))
        trace.enable(123)
        async def broken(event, data):
            raise RuntimeError("SECRET postgres://password")
        with self.assertRaises(RuntimeError):
            await module.PetDiagnosticIngress(trace)(broken, self.message(), {})
        self.assertEqual(trace.get(123).reason, "pipeline_error")
        self.assertEqual(trace.get(123).error_type, "RuntimeError")
        self.assertNotIn("SECRET", repr(trace.get(123)))

    async def test_diagnostic_failure_never_blocks_the_group_handler(self):
        module = self.types()
        class BrokenTrace:
            def begin(self, event):
                raise RuntimeError("SECRET postgres://password")
            def get(self, owner):
                raise RuntimeError("SECRET")
        async def original_handler(event, data):
            return "normal moderation result"
        with self.assertLogs("fosgen_pets.diagnostics", "WARNING") as logs:
            result = await module.PetDiagnosticIngress(BrokenTrace())(original_handler, self.message(), {})
        self.assertEqual(result, "normal moderation result")
        self.assertNotIn("SECRET", " ".join(logs.output))

    async def test_status_activates_trace_and_reports_topic_and_database_decision(self):
        module = self.types()
        trace = module.PetMessageTrace()
        store = BoundaryStore()
        store.opted_in = True
        store.initialize = AsyncMock()
        store.close = AsyncMock()
        async def record(eligibility, now=None):
            return SimpleNamespace(count=0, opted_in=True, counted=False, reason="before_consent")
        store.record_message = record
        app = SimpleNamespace(dp=Dispatcher(), ALLOWED_CHATS=[-1001], pending_users={})
        async def collect_text_messages(message):
            return None
        app.collect_text_messages = collect_text_messages
        app.dp.message.register(collect_text_messages, F.text)
        app.dp.message.outer_middleware(module.PetDiagnosticIngress(trace))
        from fosgen_pets.runtime import start_pet_runtime
        with patch("fosgen_pets.runtime.PostgresPetStore", return_value=store):
            runtime = await start_pet_runtime(app, database_url="postgresql://local", trace=trace, environ={
                "FOSGEN_PETS_ENABLED": "1", "FOSGEN_PETS_CHAT_IDS": "-1001",
                "FOSGEN_PETTEST_ADMIN_IDS": "123"})
        self.assertIsNotNone(runtime)
        self.addAsyncCleanup(runtime.stop)
        session = RecordingSession()
        bot = Bot("777:" + "a" * 35, session=session)
        self.addAsyncCleanup(bot.session.close)
        status = Message(message_id=9, date=datetime.now(timezone.utc), chat=Chat(id=123, type="private"),
                         from_user=User(id=123, first_name="User", is_bot=False), text="/pet_status")
        await app.dp.feed_update(bot, Update(update_id=1, message=status))
        await app.dp.feed_update(bot, Update(update_id=2, message=self.message()))
        await app.dp.feed_update(bot, Update(update_id=3, message=status.model_copy(update={"message_id": 10})))
        replies = [r for r in session.requests if isinstance(r, SendMessage)]
        self.assertIn("4321", replies[-1].text)
        self.assertIn("before_consent", replies[-1].text)
        self.assertIn("collect_text_messages", replies[-1].text)
        self.assertNotIn("PRIVATE RAW MESSAGE", replies[-1].text)
        pause = status.model_copy(update={"text": "/pet_pause", "message_id": 11})
        await app.dp.feed_update(bot, Update(update_id=4, message=pause))
        self.assertIsNone(trace.get(123))
        await app.dp.feed_update(bot, Update(update_id=5, message=self.message(2)))
        self.assertIsNone(trace.get(123), "Paused consent must stop diagnostic metadata too")

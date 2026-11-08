"""Search runtime: real storage, fake external provider and Telegram boundary."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram import Dispatcher

from entertainment import EntertainmentService
from entertainment.models import EntertainmentSettings
from entertainment.router import EntertainmentLearningMiddleware, register_entertainment_handlers
from entertainment.search_answers import SearchFailure
from entertainment.storage.retention import SQLiteEntertainmentStorage
from tests.test_entertainment_search_answers import ANSWER
from tests.test_entertainment_topic_denylist import FakeMessage

CHAT = -1002619489118
NOW = 200_000


class FreeSearchRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'search.db'
        self.storage = SQLiteEntertainmentStorage(self.path)
        await self.storage.initialize()
        self.addAsyncCleanup(self.storage.close)
        await self.storage.save_settings(CHAT, EntertainmentSettings(enabled=True))
        self.provider = SimpleNamespace(answer=AsyncMock(return_value=ANSWER))
        self.now = NOW
        self.app = SimpleNamespace(bot=SimpleNamespace(id=999), dp=Dispatcher())
        self.env = patch.dict('os.environ', {'TAVILY_API_KEY': 'synthetic-key'})
        self.env.start()
        self.service = self.new_service()

    def new_service(self):
        service = EntertainmentService(self.app, self.storage, {CHAT}, now_fn=lambda: self.now)
        service.search_answers.provider = self.provider
        return service

    def message(self, **kwargs):
        return FakeMessage(topic_id=kwargs.pop('topic_id', 10),
                           text=kwargs.pop('text', 'Фосген, почему небо голубое?'), **kwargs)

    async def asyncTearDown(self):
        self.env.stop()
        await self.storage.close()
        self.tmp.cleanup()

    async def test_exported_service_sends_one_plain_answer_and_only_the_question_leaves(self):
        message = self.message()
        await self.service.search_answers.handle(message)
        self.assertEqual(message.replies, [ANSWER])
        self.provider.answer.assert_awaited_once_with('почему небо голубое?')
        self.assertEqual((await self.storage.memory_counts(CHAT, 10)).total, 0)
        actions = await self.storage.recent_actions(CHAT, 10, since=0)
        self.assertEqual(len(actions), 1)
        self.assertNotIn('голуб', str(actions[0].metadata))
        self.assertNotIn('synthetic-key', str(actions[0].metadata))

    async def test_failures_and_unusable_answers_have_the_exact_single_fallback(self):
        for i, failure in enumerate((SearchFailure('quota'), TimeoutError(),
                                     RuntimeError('synthetic-key private query'), None,
                                     'The sky is blue.', 'Очень длинный ответ. ' * 100)):
            self.now += 31
            self.provider.answer.side_effect = failure if isinstance(failure, Exception) else None
            self.provider.answer.return_value = failure
            message = self.message(message_id=100+i)
            await self.service.search_answers.handle(message)
            self.assertEqual(message.replies, ['Пу пу пу'])

    async def test_concurrent_duplicate_and_restart_do_not_search_or_send_again(self):
        message = self.message()
        await asyncio.gather(*(self.service.search_answers.handle(message) for _ in range(8)))
        self.assertEqual(message.replies, [ANSWER])
        await self.storage.close()
        self.storage = SQLiteEntertainmentStorage(self.path)
        await self.storage.initialize()
        service = self.new_service()
        await service.search_answers.handle(message)
        self.assertEqual(message.replies, [ANSWER])
        self.provider.answer.assert_awaited_once()

    async def test_storage_claim_is_atomic_and_scoped_to_chat(self):
        results = await asyncio.gather(*(
            self.storage.claim_search_request(CHAT, 10, 1234, NOW) for _ in range(8)))
        self.assertEqual(sum(results), 1)
        self.assertTrue(await self.storage.claim_search_request(CHAT-1, 10, 1234, NOW))
        self.assertFalse(await self.storage.claim_search_request(CHAT, 20, 1234, NOW))

    async def test_blocked_chats_topics_unsafe_bot_and_forwarded_questions_are_silent(self):
        messages = [self.message(topic_id=292358), self.message(chat_id=-999),
                    self.message(text='Фосген, почему это пздц?'),
                    self.message(text='/Фосген почему небо голубое?')]
        bot = self.message()
        bot.from_user.is_bot = True
        forwarded = self.message()
        forwarded.forward_origin = object()
        messages += [bot, forwarded]
        for message in messages:
            await self.service.search_answers.handle(message)
            self.assertEqual(message.replies, [])
        self.provider.answer.assert_not_awaited()

    async def test_disabled_text_and_quiet_hours_preserve_existing_controls(self):
        for settings in (EntertainmentSettings(enabled=False),
                         EntertainmentSettings(enabled=True, autonomous_text_enabled=False),
                         EntertainmentSettings(enabled=True, quiet_hours_start=0,
                                               quiet_hours_end=23, timezone='UTC')):
            await self.storage.save_settings(CHAT, settings)
            message = self.message()
            await self.service.search_answers.handle(message)
            self.assertEqual(message.replies, [])
        self.provider.answer.assert_not_awaited()

    async def test_repeated_questions_are_throttled_without_spending_another_credit(self):
        await self.service.search_answers.handle(self.message())
        message = self.message(message_id=2)
        await self.service.search_answers.handle(message)
        self.assertEqual(message.replies, ['Пу пу пу'])
        self.provider.answer.assert_awaited_once()

    async def test_search_handler_bypasses_learning_without_blocking_moderation_handler(self):
        register_entertainment_handlers(self.app, self.service)
        records = self.app.dp.message.handlers
        matching = []
        message = self.message()
        for record in records:
            accepted, _ = await record.check(message, bot=self.app.bot)
            if accepted:
                matching.append(record)
        self.assertEqual(len(matching), 1)
        middleware = EntertainmentLearningMiddleware(self.service)
        moderated = AsyncMock()
        await middleware(moderated, message, {})
        moderated.assert_awaited_once()
        self.assertIsNone(middleware._worker)
        await matching[0].callback(message)
        self.assertEqual(message.replies, [ANSWER])

    async def test_send_error_does_not_trigger_a_second_fallback_message(self):
        message = self.message()
        message.reply = AsyncMock(side_effect=RuntimeError('delivery uncertain'))
        await self.service.search_answers.handle(message)
        await self.new_service().search_answers.handle(message)
        self.assertEqual(message.reply.await_count, 1)

    async def test_missing_key_keeps_the_new_route_disabled(self):
        with patch.dict('os.environ', {'TAVILY_API_KEY': ''}):
            service = EntertainmentService(self.app, self.storage, {CHAT})
        message = self.message()
        self.assertFalse(service.search_answers.matches(message))
        await service.search_answers.handle(message)
        self.assertEqual(message.replies, [])

    async def test_provider_error_details_are_never_written_to_logs(self):
        self.provider.answer.side_effect = SearchFailure('synthetic-key private question')
        with self.assertLogs('entertainment.search_service', level='INFO') as logs:
            message = self.message()
            await self.service.search_answers.handle(message)
        self.assertEqual(message.replies, ['Пу пу пу'])
        self.assertNotIn('synthetic-key', ' '.join(logs.output))
        self.assertNotIn('private question', ' '.join(logs.output))

    async def test_oversized_question_does_not_spend_a_credit(self):
        message = self.message(text='Фосген, почему ' + 'небо ' * 110)
        await self.service.search_answers.handle(message)
        self.assertEqual(message.replies, ['Пу пу пу'])
        self.provider.answer.assert_not_awaited()

    async def test_busy_search_returns_one_fallback_instead_of_queueing_more_calls(self):
        started, finish = asyncio.Event(), asyncio.Event()

        async def wait_for_answer(question):
            started.set()
            await finish.wait()
            return ANSWER

        self.provider.answer.side_effect = wait_for_answer
        first = self.message()
        task = asyncio.create_task(self.service.search_answers.handle(first))
        await started.wait()
        second = self.message(message_id=2, user_id=8)
        try:
            await asyncio.wait_for(self.service.search_answers.handle(second), timeout=.5)
            self.assertEqual(second.replies, ['Пу пу пу'])
        finally:
            finish.set()
            await task
        self.assertEqual(first.replies, [ANSWER])
        self.provider.answer.assert_awaited_once()

    async def test_replay_during_storage_outage_cannot_bypass_a_durable_claim(self):
        message = self.message()
        await self.service.search_answers.handle(message)
        restarted = self.new_service()
        with patch.object(self.storage, 'get_settings', new=AsyncMock(side_effect=RuntimeError('offline'))):
            await restarted.search_answers.handle(message)
        self.assertEqual(message.replies, [ANSWER])
        self.provider.answer.assert_awaited_once()

    async def test_claim_failure_does_not_send_or_spend_without_ownership(self):
        message = self.message()
        with patch.object(self.storage, 'claim_search_request',
                          new=AsyncMock(side_effect=RuntimeError('offline'))):
            await self.service.search_answers.handle(message)
        self.assertEqual(message.replies, [])
        self.provider.answer.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()

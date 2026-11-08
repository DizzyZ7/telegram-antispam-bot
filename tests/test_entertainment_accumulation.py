"""Age-gated creative memory through the actual exported service."""
import random
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from unittest.mock import AsyncMock

from entertainment import EntertainmentService
from entertainment.culture import build_culture_context
from entertainment.culture import CultureGenerationContext
from entertainment.generation_v3 import GenerationResult
from entertainment.models import MemoryEvent, MemoryEventType, EntertainmentActionType
from entertainment.autonomy import ActionCandidate
from entertainment.context_interjection import ContextInterjection
from entertainment.storage.windows import SQLiteEntertainmentStorage

NOW = 2_000_000
CUTOFF = NOW - 86_400
CHAT, TOPIC = -1001, 10


def event(mid, text, *, at=CUTOFF, user=7, topic=TOPIC, bot=False, forwarded=False, command=False):
    return MemoryEvent(id=None, chat_id=CHAT, topic_id=topic, message_id=mid,
                       user_id=user, event_type=MemoryEventType.TEXT, created_at=at,
                       text=text, sender_is_bot=bot, is_forwarded=forwarded, is_command=command)


class AccumulationContextTests(unittest.TestCase):
    def test_fresh_conversation_anchors_topic_without_supplying_fragments(self):
        context = build_culture_context([
            event(1, 'Чайник требует официальный выходной', at=CUTOFF),
            event(2, 'Дедлайн уже уехал на дачу', at=CUTOFF + 1),
        ], [], generation_before=CUTOFF)
        self.assertIn('Чайник требует официальный выходной', context.source_messages)
        self.assertFalse(any('уехал' in s for s in context.source_messages))
        self.assertIn('Дедлайн уже уехал на дачу', context.context_messages)
        self.assertIn('Дедлайн уже уехал на дачу', context.fresh_messages)
        self.assertIn('Чайник требует официальный выходной', context.protected_messages)

    def test_old_bot_forward_and_command_texts_do_not_become_creative_sources(self):
        context = build_culture_context([
            event(1, 'Кофе охраняет маленький холодильник'),
            event(2, 'Бот распространяет секретные инструкции', bot=True),
            event(3, 'Пересланный текст изображает новости', forwarded=True),
            event(4, '/чужаякоманда проверяет загрузку', command=True),
        ], [], generation_before=CUTOFF)
        self.assertIn('Кофе охраняет маленький холодильник', context.source_messages)
        self.assertFalse(any('инструкции' in s or 'новости' in s or 'загрузку' in s
                             for s in context.source_messages))


class AccumulationStorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'memory.db'
        self.storage = SQLiteEntertainmentStorage(self.path)
        await self.storage.initialize()

    async def asyncTearDown(self):
        await self.storage.close()
        self.tmp.cleanup()

    def service(self, now=NOW):
        return EntertainmentService(SimpleNamespace(bot=SimpleNamespace(id=999)),
                                    self.storage, {CHAT}, rng=random.Random(4),
                                    now_fn=lambda: now, birthday_plans=[])

    async def test_exported_service_excludes_today_even_when_recent_memory_is_full(self):
        await self.storage.add_event(event(1, 'Чайник требует официальный выходной'))
        for mid in range(2, 40):
            await self.storage.add_event(event(mid, f'Свежая уникальная реплика номер {mid}', at=NOW - mid))
        with patch('entertainment.culture_service.GENERATION_SAMPLE_LIMIT', 2):
            snapshot = await self.service()._culture_memory_snapshot(CHAT, TOPIC, now=NOW)
        self.assertEqual(len(snapshot.recent_events), 2)
        self.assertTrue(snapshot.generation.source_messages)
        self.assertFalse(any('Свежая' in s for s in snapshot.generation.source_messages))
        self.assertIn('Чайник требует официальный выходной', snapshot.generation.source_messages)

    async def test_age_filtered_windows_stay_bounded_and_scope_isolated(self):
        await self.storage.add_event(event(1, 'Старая реплика', at=CUTOFF - 1))
        await self.storage.add_event(event(2, 'Граничная реплика', at=CUTOFF))
        await self.storage.add_event(event(3, 'Еще свежая реплика', at=CUTOFF + 1))
        await self.storage.add_event(event(4, 'Чужая тема', at=CUTOFF - 1, topic=99))
        windows = await self.storage.sample_event_windows(CHAT, TOPIC, window_count=4,
                            window_size=16, seed=42, created_before=CUTOFF)
        self.assertEqual([[e.message_id for e in w] for w in windows], [[1, 2]])

    async def test_message_matures_after_a_day_and_survives_reopening(self):
        await self.storage.add_event(event(1, 'Холодильник просит еще один выходной', at=NOW))
        initial = await self.service()._culture_memory_snapshot(CHAT, TOPIC, now=NOW)
        self.assertEqual(initial.generation.source_messages, [])
        await self.storage.close()
        self.storage = SQLiteEntertainmentStorage(self.path)
        await self.storage.initialize()
        later = await self.service(NOW + 86_400)._culture_memory_snapshot(CHAT, TOPIC, now=NOW + 86_400)
        self.assertIn('Холодильник просит еще один выходной', later.generation.source_messages)
        await self.storage.delete_user_memory(CHAT, 7)
        forgotten = await self.service(NOW + 86_400)._culture_memory_snapshot(CHAT, TOPIC, now=NOW + 86_400)
        self.assertEqual(forgotten.generation.source_messages, [])

    async def test_exported_generator_remixes_old_learned_actions_after_restart(self):
        sources = ['Старая кастрюля просит дополнительный выходной',
                   'Ленивый будильник уехал на дальнюю дачу',
                   'Голодные коты охраняют последние пельмени']
        for mid, text in enumerate(sources, 1):
            await self.storage.add_event(event(mid, text, at=NOW - 30 * 86_400))
        await self.storage.add_event(event(5, 'Сегодня все обсуждают погоду', at=NOW))
        await self.storage.close()
        self.storage = SQLiteEntertainmentStorage(self.path)
        await self.storage.initialize()
        service = self.service()
        snapshot = await service._culture_memory_snapshot(CHAT, TOPIC, now=NOW)
        outcome = service._generate_culture_text(snapshot.generation, recent_outputs=[],
                                                 recent_signatures=set())
        self.assertIsNotNone(outcome.text)
        self.assertNotIn(outcome.text.rstrip('.'), sources)
        self.assertNotIn('погоду', outcome.text)
        self.assertRegex(outcome.text.casefold(), r'прос|уеха|охраня')

    async def test_working_generator_applies_fresh_fragment_guard_to_legacy_candidates(self):
        context = CultureGenerationContext(
            source_messages=['old server goes offline', 'the other build went green'],
            context_messages=[],
            fresh_messages=['Я опять забыл поесть сегодня'],
        )
        candidate = GenerationResult('Чайник опять забыл поесть сегодня и требует выходной.',
                                     'v3', 5, 1, {})
        with patch('entertainment.culture_service.generate_text', return_value=candidate):
            result = self.service()._generate_culture_text(context, recent_outputs=[],
                                                           recent_signatures=set())
        self.assertIsNone(result.text)

    async def test_ready_learned_message_is_not_replaced_by_a_curated_scene(self):
        for mid, text in enumerate([
            'Старая кастрюля просит дополнительный выходной',
            'Ленивый будильник уехал на дальнюю дачу',
            'Голодные коты охраняют последние пельмени',
        ], 1):
            await self.storage.add_event(event(mid, text))
        for mid in range(10, 40):
            await self.storage.add_message(CHAT, TOPIC, 7, 'Давно обсуждали новую главу',
                                           message_id=mid, created_at=CUTOFF)
        for mid, user, text in [(50, 8, 'Пишу новую главу'), (51, 9, 'Эта глава почти готова')]:
            await self.storage.add_event(event(mid, text, user=user, at=NOW - 10))
            await self.storage.add_message(CHAT, TOPIC, user, text, message_id=mid, created_at=NOW - 10)
        service = self.service()
        message = SimpleNamespace(chat=SimpleNamespace(id=CHAT, type='supergroup'),
                                  from_user=SimpleNamespace(id=9, is_bot=False),
                                  text='Эта глава почти готова', caption=None,
                                  message_thread_id=TOPIC, message_id=51,
                                  reply_to_message=None, reply=AsyncMock())
        slot = ActionCandidate(EntertainmentActionType.REMIXED_PHRASE, .99, .9, .01)
        with (patch('entertainment.culture_service.select_action', return_value=slot),
              patch('entertainment.culture_service.choose_context_interjection',
                    return_value=ContextInterjection('Готовая сценка про сюжет и комиссию.', 'writing')),
              patch.object(service.rng, 'random', return_value=0.0)):
            record = await service.evaluate_topic(message)
        self.assertIsNotNone(record)
        self.assertEqual(record.metadata['generation_origin'], 'memory')
        self.assertNotIn('Готовая сценка', record.metadata['output'])

    async def test_month_old_ordinary_messages_supply_vocabulary_after_reopening(self):
        sources = ['Поставил чайник на плиту', 'Дописываю последнюю главу',
                   'Купил свежие пельмени', 'Ищу старый будильник',
                   'Забыл выключить телевизор', 'Открыл маленький холодильник']
        for mid, text in enumerate(sources, 1):
            await self.storage.add_event(event(mid, text, at=NOW - 30 * 86_400))
        await self.storage.add_event(event(10, 'Собрал красивый дирижабль', at=NOW))
        await self.storage.close()
        self.storage = SQLiteEntertainmentStorage(self.path)
        await self.storage.initialize()
        service = self.service()
        snapshot = await service._culture_memory_snapshot(CHAT, TOPIC, now=NOW)
        outcome = service._generate_culture_text(snapshot.generation, recent_outputs=[],
                                                 recent_signatures=set())
        self.assertIsNotNone(outcome.text)
        self.assertEqual(outcome.diagnostics.origin, 'memory')
        self.assertNotIn('дирижабл', outcome.text.casefold())
        await self.storage.delete_user_memory(CHAT, 7)
        forgotten = await service._culture_memory_snapshot(CHAT, TOPIC, now=NOW)
        self.assertIsNone(service._generate_culture_text(
            forgotten.generation, recent_outputs=[], recent_signatures=set()).text)

    async def test_vocabulary_review_cases_pass_through_the_exported_generation_gate(self):
        service = self.service()
        for actor in ('Позвал нового гения', 'Поговорил с новым главой', 'Очень рады'):
            with self.subTest(actor=actor):
                context = CultureGenerationContext(
                    source_messages=[actor, 'Я опять хочу спать', 'Я давно ушел отдыхать'],
                    context_messages=[])
                outcome = service._generate_culture_text(context, recent_outputs=[], recent_signatures=set())
                if outcome.text is not None:
                    self.assertNotEqual(outcome.diagnostics.origin, 'memory')
        context = CultureGenerationContext(
            source_messages=['Старая клавиатура', 'Было страшно снова звонить'], context_messages=[])
        self.assertIsNone(service._generate_culture_text(
            context, recent_outputs=[], recent_signatures=set()).text)

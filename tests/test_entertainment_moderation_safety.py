from __future__ import annotations

import random
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from entertainment import EntertainmentService
from entertainment.culture import CultureGenerationContext
from entertainment.generation_v3 import GenerationResult
from entertainment.models import EntertainmentSettings, MemoryCounts, MemoryEvent, MemoryEventType
from writers_moderation import contains_prohibited_language


def obj(**kwargs):
    return SimpleNamespace(**kwargs)


def make_message(*, text: str, message_id: int = 500):
    return obj(
        chat=obj(id=-1001, type="supergroup"),
        from_user=obj(id=7, is_bot=False),
        message_thread_id=10,
        message_id=message_id,
        text=text,
        caption=None,
        sticker=None,
        photo=None,
        animation=None,
        voice=None,
        video=None,
        document=None,
        reply_to_message=None,
        forward_origin=None,
        forward_date=None,
        replies=[],
        reply=AsyncMock(),
    )


def memory_event(*, message_id: int, text: str, created_at: int) -> MemoryEvent:
    return MemoryEvent(
        id=message_id,
        chat_id=-1001,
        topic_id=10,
        message_id=message_id,
        user_id=7,
        event_type=MemoryEventType.TEXT,
        created_at=created_at,
        text=text,
    )


def result(text: str) -> GenerationResult:
    return GenerationResult(
        text=text,
        engine="v3",
        score=4.0,
        candidate_count=8,
        rejection_counts={},
    )


class EntertainmentModerationSafetyTests(unittest.IsolatedAsyncioTestCase):
    def service(self, storage) -> EntertainmentService:
        service = EntertainmentService(
            obj(bot=obj(id=999)),
            storage,
            {-1001},
            blocked_topic_scopes=(),
            rng=random.Random(1),
            now_fn=lambda: 12_345.0,
        )
        service.evaluate_topic = AsyncMock(return_value=None)
        return service

    async def test_prohibited_text_is_never_learned(self):
        storage = obj(
            get_settings=AsyncMock(return_value=EntertainmentSettings(enabled=True)),
            get_remember_enabled=AsyncMock(return_value=True),
            add_event=AsyncMock(return_value=1),
            add_message=AsyncMock(),
        )
        service = self.service(storage)

        await service.observe_message(make_message(text="ну это пздц конечно сегодня"))

        storage.add_event.assert_not_awaited()
        storage.add_message.assert_not_awaited()
        service.evaluate_topic.assert_not_awaited()

    async def test_dirty_historical_rows_are_excluded_from_generation_snapshot(self):
        dirty = memory_event(
            message_id=1,
            text="вот это пздц какая история сегодня",
            created_at=100,
        )
        safe = memory_event(
            message_id=2,
            text="художник закончил новую иллюстрацию сегодня",
            created_at=101,
        )
        storage = obj(
            recent_events=AsyncMock(return_value=[dirty, safe]),
            sample_event_windows=AsyncMock(return_value=[[dirty, safe]]),
            memory_counts=AsyncMock(return_value=MemoryCounts(total=100, text=100)),
        )
        service = self.service(storage)

        snapshot = await service._culture_memory_snapshot(-1001, 10, now=200)

        self.assertTrue(snapshot.generation.source_messages)
        self.assertTrue(
            any("иллюстрац" in value.casefold() for value in snapshot.generation.source_messages)
        )
        self.assertFalse(
            any(contains_prohibited_language(value) for value in snapshot.generation.source_messages)
        )
        self.assertFalse(
            any(contains_prohibited_language(value) for value in snapshot.generation.context_messages)
        )

    async def test_generated_prohibited_candidate_is_rejected_and_retried(self):
        storage = obj()
        service = self.service(storage)
        context = CultureGenerationContext(
            source_messages=[
                "писатели обсуждают новую главу вечером",
                "художники рисуют обложку для истории",
            ],
            context_messages=["обсуждаем новую работу"],
        )
        unsafe = "вот это пздц какая странная история сегодня"
        safe = "сюжет решил свернуть туда где его никто не ждал"

        with patch(
            "entertainment.culture_service.generate_text",
            side_effect=[result(unsafe), result(safe)],
        ) as mocked_generate:
            outcome = service._generate_culture_text(
                context,
                recent_outputs=[],
                recent_signatures=set(),
            )

        self.assertEqual(outcome.text, safe)
        self.assertFalse(contains_prohibited_language(outcome.text or ""))
        self.assertEqual(mocked_generate.call_count, 2)


if __name__ == "__main__":
    unittest.main()

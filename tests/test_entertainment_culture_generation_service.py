from __future__ import annotations

import random
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from entertainment.autonomy import ActionCandidate
from entertainment.config import GENERATION_SAMPLE_LIMIT
from entertainment.context import ActivitySnapshot
from entertainment.models import (
    EntertainmentActionType,
    EntertainmentSettings,
    MemoryEvent,
    MemoryEventType,
)
from entertainment.service import EntertainmentService


def obj(**kwargs):
    return SimpleNamespace(**kwargs)


def event(message_id: int, text: str, *, created_at: int = 1000) -> MemoryEvent:
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


def message(*, text: str = "автобус скоро будет", direct: bool = False):
    replied = (
        obj(from_user=obj(id=999, is_bot=True), message_id=444)
        if direct
        else None
    )
    return obj(
        chat=obj(id=-1001, type="supergroup"),
        from_user=obj(id=7, is_bot=False),
        message_thread_id=10,
        message_id=500,
        text=text,
        reply_to_message=replied,
        reply=AsyncMock(),
    )


def storage():
    return SimpleNamespace(
        get_settings=AsyncMock(return_value=EntertainmentSettings(enabled=True)),
        message_count=AsyncMock(return_value=100),
        activity_snapshot=AsyncMock(
            return_value=ActivitySnapshot(
                chat_id=-1001,
                topic_id=10,
                messages_1m=1,
                messages_5m=2,
                messages_previous_5m=0,
                messages_15m=3,
                active_users_5m=2,
                seconds_since_human=5.0,
            )
        ),
        recent_actions=AsyncMock(return_value=[]),
        human_messages_since=AsyncMock(return_value=10),
        recent_events=AsyncMock(
            return_value=[
                event(1, "автобус ночью едет 😂", created_at=1000),
                event(2, "до вокзала еще далеко", created_at=1010),
            ]
        ),
        sample_event_windows=AsyncMock(
            return_value=[[event(101, "старый мем про метро", created_at=100)]]
        ),
        recent_texts=AsyncMock(return_value=["legacy fallback text"] * 30),
        recent_messages=AsyncMock(return_value=["legacy message"] * 30),
        record_action=AsyncMock(return_value=1),
    )


class CultureGenerationServiceTests(unittest.IsolatedAsyncioTestCase):
    def service(self, store) -> EntertainmentService:
        return EntertainmentService(
            obj(bot=obj(id=999, send_message=AsyncMock())),
            store,
            {-1001},
            rng=random.Random(4),
            now_fn=lambda: 5000.0,
        )

    async def test_culture_context_reads_only_bounded_recent_and_historical_windows(self):
        store = storage()
        service = self.service(store)

        context = await service._culture_generation_context(
            -1001,
            10,
            trigger_text="где автобус",
            now=5000,
        )

        store.recent_events.assert_awaited_once_with(-1001, 10, GENERATION_SAMPLE_LIMIT)
        call = store.sample_event_windows.await_args
        self.assertEqual(call.args, (-1001, 10))
        self.assertEqual(call.kwargs["window_count"], 4)
        self.assertEqual(call.kwargs["window_size"], 16)
        self.assertIsInstance(call.kwargs["seed"], int)
        store.recent_texts.assert_not_awaited()
        store.recent_messages.assert_not_awaited()
        self.assertEqual(context.context_messages[-1], "где автобус")
        self.assertIn("старый мем про метро", context.source_messages)

    async def test_culture_is_not_read_when_autonomy_declines_action(self):
        store = storage()
        service = self.service(store)

        with patch("entertainment.service.select_action", return_value=None):
            result = await service.evaluate_topic(message())

        self.assertIsNone(result)
        store.recent_events.assert_not_awaited()
        store.sample_event_windows.assert_not_awaited()

    async def test_direct_reply_text_becomes_generation_context_anchor(self):
        store = storage()
        service = self.service(store)
        selected = ActionCandidate(
            action_type=EntertainmentActionType.CONTEXTUAL_REPLY,
            relevance=1.0,
            novelty=1.0,
            annoyance_cost=0.0,
            trigger_message_id=500,
        )

        with (
            patch("entertainment.service.select_action", return_value=selected),
            patch.object(service, "_generate_culture_text", return_value=("готовая фраза", None)) as generate,
        ):
            await service.evaluate_topic(message(text="где наш автобус", direct=True))

        context = generate.call_args.args[0]
        self.assertEqual(context.context_messages[-1], "где наш автобус")
        self.assertGreaterEqual(context.context_messages.count("где наш автобус"), 2)

    async def test_generate_culture_text_passes_context_to_phrase_engine_and_keeps_novelty(self):
        store = storage()
        service = self.service(store)
        context = await service._culture_generation_context(-1001, 10, trigger_text="автобус", now=5000)

        with (
            patch("entertainment.service.generate_chat_text", return_value="совсем новая фраза") as generator,
            patch("entertainment.service.is_novel_generated_text", return_value=True) as novelty,
            patch("entertainment.service.apply_emoji_style", return_value=("совсем новая фраза 😂", "😂")),
        ):
            generated, signature = service._generate_culture_text(
                context,
                recent_outputs=["старый ответ"],
                recent_signatures=set(),
            )

        self.assertEqual(generated, "совсем новая фраза 😂")
        self.assertEqual(signature, "😂")
        self.assertEqual(generator.call_args.kwargs["context_messages"], context.context_messages)
        novelty.assert_called_once_with(
            "совсем новая фраза",
            context.source_messages,
            ["старый ответ"],
        )

    async def test_action_metadata_records_culture_diagnostics_without_raw_corpus(self):
        store = storage()
        service = self.service(store)
        selected = ActionCandidate(
            action_type=EntertainmentActionType.REMIXED_PHRASE,
            relevance=1.0,
            novelty=1.0,
            annoyance_cost=0.0,
        )

        with (
            patch("entertainment.service.select_action", return_value=selected),
            patch.object(service, "_generate_culture_text", return_value=("мемная фраза 😂", "😂")),
        ):
            record = await service.evaluate_topic(message())

        self.assertIsNotNone(record)
        metadata = store.record_action.await_args.args[0].metadata
        self.assertEqual(metadata["emoji_signature"], "😂")
        self.assertEqual(metadata["culture_recent_events"], 2)
        self.assertEqual(metadata["culture_historical_events"], 1)
        self.assertNotIn("culture_corpus", metadata)
        self.assertNotIn("context_messages", metadata)


if __name__ == "__main__":
    unittest.main()

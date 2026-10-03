from __future__ import annotations

import random
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from entertainment import EntertainmentService
from entertainment.autonomy import ActionCandidate
from entertainment.config import GENERATION_SAMPLE_LIMIT
from entertainment.context import ActivitySnapshot
from entertainment.generation_v3 import GenerationMode, GenerationResult
from entertainment.models import (
    EntertainmentActionType,
    EntertainmentSettings,
    MemoryEvent,
    MemoryEventType,
)


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
        memory_counts=AsyncMock(),
        recent_texts=AsyncMock(return_value=["legacy fallback text"] * 30),
        recent_messages=AsyncMock(return_value=["legacy message"] * 30),
        record_action=AsyncMock(return_value=1),
    )


def generated_result(
    text: str = "совсем новая фраза",
    *,
    score: float = 5.25,
    candidate_count: int = 9,
) -> GenerationResult:
    return GenerationResult(
        text=text,
        engine="v3",
        score=score,
        candidate_count=candidate_count,
        rejection_counts={"exact_source": 2, "single_source": 1},
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
        store.memory_counts.return_value = obj(total=3, text=3, emoji=0, sticker=0, photo=0, animation=0)
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

        with patch("entertainment.culture_service.select_action", return_value=None):
            result = await service.evaluate_topic(message())

        self.assertIsNone(result)
        store.recent_events.assert_not_awaited()
        store.sample_event_windows.assert_not_awaited()

    async def test_direct_reply_passes_exact_trigger_separately_to_v3(self):
        store = storage()
        store.memory_counts.return_value = obj(total=3, text=3, emoji=0, sticker=0, photo=0, animation=0)
        service = self.service(store)
        selected = ActionCandidate(
            action_type=EntertainmentActionType.CONTEXTUAL_REPLY,
            relevance=1.0,
            novelty=1.0,
            annoyance_cost=0.0,
            trigger_message_id=500,
        )

        with (
            patch("entertainment.culture_service.select_action", return_value=selected),
            patch("entertainment.culture_service.generate_text", return_value=generated_result("готовая фраза")) as generator,
            patch("entertainment.culture_service.is_novel_generated_text", return_value=True),
            patch("entertainment.culture_service.apply_emoji_style", return_value=("готовая фраза", None)),
            patch("entertainment.culture_service.select_media_candidate", return_value=None),
        ):
            await service.evaluate_topic(message(text="где наш автобус", direct=True))

        request = generator.call_args.args[0]
        self.assertEqual(request.mode, GenerationMode.DIRECT_REPLY)
        self.assertEqual(request.trigger_text, "где наш автобус")
        self.assertEqual(request.context_messages[-1], "где наш автобус")
        self.assertGreaterEqual(request.context_messages.count("где наш автобус"), 2)

    async def test_generate_culture_text_builds_v3_request_then_applies_emoji(self):
        store = storage()
        store.memory_counts.return_value = obj(total=3, text=3, emoji=0, sticker=0, photo=0, animation=0)
        service = self.service(store)
        context = await service._culture_generation_context(-1001, 10, trigger_text="автобус", now=5000)
        result = generated_result()

        with (
            patch("entertainment.culture_service.generate_text", return_value=result) as generator,
            patch("entertainment.culture_service.is_novel_generated_text", return_value=True) as novelty,
            patch("entertainment.culture_service.apply_emoji_style", return_value=("совсем новая фраза 😂", "😂")) as emoji,
        ):
            outcome = service._generate_culture_text(
                context,
                recent_outputs=["старый ответ"],
                recent_signatures=set(),
                mode=GenerationMode.DIRECT_REPLY,
                trigger_text="автобус",
            )
            generated, signature = outcome

        self.assertEqual(generated, "совсем новая фраза 😂")
        self.assertEqual(signature, "😂")
        self.assertEqual(outcome.diagnostics, result)
        request = generator.call_args.args[0]
        self.assertEqual(request.source_messages, context.source_messages)
        self.assertEqual(request.context_messages, context.context_messages)
        self.assertEqual(request.trigger_text, "автобус")
        self.assertEqual(request.mode, GenerationMode.DIRECT_REPLY)
        self.assertEqual(request.recent_bot_outputs, ["старый ответ"])
        novelty.assert_called_once_with(
            "совсем новая фраза",
            context.source_messages,
            ["старый ответ"],
        )
        emoji.assert_called_once()

    async def test_action_metadata_records_safe_generation_diagnostics_without_raw_corpus(self):
        store = storage()
        store.memory_counts.return_value = obj(total=3, text=3, emoji=0, sticker=0, photo=0, animation=0)
        service = self.service(store)
        selected = ActionCandidate(
            action_type=EntertainmentActionType.REMIXED_PHRASE,
            relevance=1.0,
            novelty=1.0,
            annoyance_cost=0.0,
        )
        diagnostics = generated_result("мемная фраза", score=5.25, candidate_count=9)

        with (
            patch("entertainment.culture_service.select_action", return_value=selected),
            patch("entertainment.culture_service.generate_text", return_value=diagnostics),
            patch("entertainment.culture_service.is_novel_generated_text", return_value=True),
            patch("entertainment.culture_service.apply_emoji_style", return_value=("мемная фраза 😂", "😂")),
            patch("entertainment.culture_service.select_media_candidate", return_value=None),
        ):
            record = await service.evaluate_topic(message())

        self.assertIsNotNone(record)
        metadata = store.record_action.await_args.args[0].metadata
        self.assertEqual(metadata["emoji_signature"], "😂")
        self.assertEqual(metadata["culture_recent_events"], 2)
        self.assertEqual(metadata["culture_historical_events"], 1)
        self.assertEqual(metadata["generation_engine"], "v3")
        self.assertEqual(metadata["generation_mode"], "autonomous")
        self.assertEqual(metadata["generation_candidate_count"], 9)
        self.assertEqual(metadata["generation_score_bucket"], "high")
        self.assertEqual(
            metadata["generation_rejections"],
            {"exact_source": 2, "single_source": 1},
        )
        self.assertNotIn("culture_corpus", metadata)
        self.assertNotIn("context_messages", metadata)
        self.assertNotIn("trigger_text", metadata)

    async def test_manual_generate_uses_autonomous_mode(self):
        store = storage()
        store.memory_counts.return_value = obj(total=3, text=3, emoji=0, sticker=0, photo=0, animation=0)
        service = self.service(store)
        diagnostics = generated_result("готовая фраза")

        with (
            patch("entertainment.culture_service.generate_text", return_value=diagnostics) as generator,
            patch("entertainment.culture_service.is_novel_generated_text", return_value=True),
            patch("entertainment.culture_service.apply_emoji_style", return_value=("готовая фраза", None)),
        ):
            await service.generate_now(message())

        request = generator.call_args.args[0]
        self.assertEqual(request.mode, GenerationMode.AUTONOMOUS)
        self.assertIsNone(request.trigger_text)


if __name__ == "__main__":
    unittest.main()

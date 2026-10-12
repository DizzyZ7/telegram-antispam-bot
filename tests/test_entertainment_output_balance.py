"""Regression: text/media parity without extra actions or chat leakage."""
from __future__ import annotations

import random
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from entertainment.autonomy import ActionCandidate
from entertainment.context import ActivitySnapshot
from entertainment.culture import CultureGenerationContext, CultureMemorySnapshot
from entertainment.culture_service import CultureGenerationOutcome
from entertainment.culture_service import EntertainmentService as CultureService
from entertainment.models import (
    EntertainmentActionRecord, EntertainmentActionType,
    EntertainmentSettings, MemoryCounts, MemoryEvent, MemoryEventType,
)
from entertainment.output_balance import recent_literary_context, should_send_media


CHAT = -1002619489118
TOPIC = 12345
NOW = 100000


class FixedRng(random.Random):
    def __init__(self, probability: float):
        super().__init__(1)
        self.probability = probability

    def random(self):
        return self.probability

    def shuffle(self, items):
        return None

    def choice(self, items):
        return items[0]


def action(kind, *, created_at=NOW - 60, topic=TOPIC):
    return EntertainmentActionRecord(
        id=123, chat_id=CHAT, topic_id=topic,
        action_type=kind, trigger_message_id=None,
        created_at=created_at, metadata={},
    )


def event(text="Дописать главу сегодня", *, created_at=NOW - 12,
          chat=CHAT, topic=TOPIC, sender_is_bot=False, forwarded=False,
          command=False):
    return MemoryEvent(
        id=2, chat_id=chat, topic_id=topic, message_id=2,
        user_id=77, event_type=MemoryEventType.TEXT,
        created_at=created_at, text=text,
        sender_is_bot=sender_is_bot, is_forwarded=forwarded,
        is_command=command,
    )


def picture():
    return MemoryEvent(
        id=3, chat_id=CHAT, topic_id=TOPIC, message_id=3,
        user_id=77, event_type=MemoryEventType.PHOTO,
        created_at=NOW - 100, file_id="photo-telegram",
        file_unique_id="photo-unq",
    )


class BalanceUnitTests(unittest.TestCase):
    def test_media_no_longer_preempts_valid_text_unconditionally(self):
        history = [
            action(EntertainmentActionType.MEMORY_CALLBACK),
            action(EntertainmentActionType.REMIXED_PHRASE),
        ]
        self.assertFalse(should_send_media(
            history, text_ready=True, direct_reply=False, rng=FixedRng(0),
        ))

    def test_media_share_stays_below_ceiling_with_text(self):
        history = [
            action(EntertainmentActionType.REMIXED_PHRASE),
            action(EntertainmentActionType.MEMORY_CALLBACK),
            action(EntertainmentActionType.REMIXED_PHRASE),
        ]
        # A second media out of four would be 50%, above the 45% ceiling.
        self.assertFalse(should_send_media(
            history, text_ready=True, direct_reply=False, rng=FixedRng(0),
        ))
        history = [action(EntertainmentActionType.REMIXED_PHRASE)] * 4
        self.assertTrue(should_send_media(
            history, text_ready=True, direct_reply=False, rng=FixedRng(0),
        ))
        self.assertFalse(should_send_media(
            history, text_ready=True, direct_reply=True, rng=FixedRng(0.20),
        ))

    def test_media_is_still_available_without_a_text_candidate(self):
        for history in (
            [],
            [action(EntertainmentActionType.MEMORY_CALLBACK)] * 10,
        ):
            self.assertTrue(should_send_media(
                history, text_ready=False, direct_reply=False,
                rng=FixedRng(0.99),
            ))

    def test_media_is_not_disabled_entirely_when_text_exists(self):
        self.assertTrue(should_send_media(
            [], text_ready=True, direct_reply=False, rng=FixedRng(0.0),
        ))
        self.assertFalse(should_send_media(
            [], text_ready=True, direct_reply=False, rng=FixedRng(0.99),
        ))

    def test_recovery_uses_only_fresh_human_local_literary_context(self):
        self.assertEqual(
            recent_literary_context([event()], now=NOW),
            "Дописать главу сегодня",
        )
        self.assertIsNone(recent_literary_context(
            [event(created_at=NOW-1300)], now=NOW,
        ))
        self.assertIsNone(recent_literary_context(
            [event(sender_is_bot=True)], now=NOW,
        ))
        self.assertIsNone(recent_literary_context(
            [event(forwarded=True)], now=NOW,
        ))
        self.assertIsNone(recent_literary_context(
            [event(command=True)], now=NOW,
        ))
        self.assertIsNone(recent_literary_context(
            [event(text="Как погода?")], now=NOW,
        ))
        self.assertIsNone(recent_literary_context(
            [event(created_at=NOW+10)], now=NOW,
        ))
        self.assertEqual(recent_literary_context(
            [event(text="Глава готова", created_at=NOW-5),
             event(text="Привет", created_at=NOW-2)], now=NOW,
        ), "Глава готова")


class BalancedServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.storage = SimpleNamespace(
            get_settings=AsyncMock(return_value=EntertainmentSettings()),
            message_count=AsyncMock(return_value=100),
            activity_snapshot=AsyncMock(return_value=ActivitySnapshot(
                chat_id=CHAT, topic_id=TOPIC, messages_1m=2, messages_5m=3,
                messages_previous_5m=2, messages_15m=5,
                active_users_5m=2, seconds_since_human=10,
            )),
            recent_actions=AsyncMock(return_value=[]),
            human_messages_since=AsyncMock(return_value=5),
            record_action=AsyncMock(return_value=456),
            get_remember_enabled=AsyncMock(return_value=False),
        )
        self.app = SimpleNamespace(bot=SimpleNamespace(id=9988))
        self.service = CultureService(
            self.app, self.storage, {CHAT},
            rng=FixedRng(0.99), now_fn=lambda: NOW,
        )
        self.message = SimpleNamespace(
            chat=SimpleNamespace(id=CHAT, type="supergroup"),
            from_user=SimpleNamespace(id=7, is_bot=False),
            message_id=234, message_thread_id=TOPIC,
            text="Глава готова", reply=AsyncMock(),
        )
        self.snapshot = CultureMemorySnapshot(
            recent_events=[event(), picture()],
            historical_windows=[],
            counts=MemoryCounts(total=20, text=15, photo=1),
            generation=CultureGenerationContext(
                source_messages=["первая глава была", "вторая глава готова"],
                context_messages=["Скоро новая глава"],
            ),
        )
        self.service._culture_memory_snapshot = AsyncMock(return_value=self.snapshot)
        self.service._send_text_fallback = AsyncMock()
        self.service._send_media_candidate = AsyncMock()

    async def run_slot(self, text, *, history=None, random_value=0.99):
        self.service.rng = FixedRng(random_value)
        self.storage.recent_actions.return_value = history or []
        self.service._generate_culture_text = Mock(return_value=CultureGenerationOutcome(
            text, None, None,
        ))
        candidate = SimpleNamespace(
            event=picture(), source_class="human", score_bucket="high",
        )
        with patch("entertainment.culture_service.select_action", return_value=ActionCandidate(
            action_type=EntertainmentActionType.REMIXED_PHRASE,
            relevance=1, novelty=1, annoyance_cost=0,
        )), patch("entertainment.culture_service.select_media_candidate", return_value=candidate):
            return await self.service.evaluate_topic(self.message)

    async def test_when_media_was_last_output_available_text_is_delivered(self):
        record = await self.run_slot(
            "Оригинальная веселая литературная фраза",
            history=[action(EntertainmentActionType.MEMORY_CALLBACK)],
            random_value=0,
        )
        self.assertEqual(record.metadata["output_kind"], "text")
        self.service._send_text_fallback.assert_awaited_once()
        self.service._send_media_candidate.assert_not_awaited()
        self.storage.record_action.assert_awaited_once()

    async def test_media_remains_possible_but_not_mandatory(self):
        record = await self.run_slot("Уместный текст", random_value=0)
        self.assertEqual(record.action_type, EntertainmentActionType.MEMORY_CALLBACK)
        self.assertEqual(record.metadata["output_kind"], "media")
        self.service._send_media_candidate.assert_awaited_once()
        self.service._send_text_fallback.assert_not_awaited()

    async def test_when_generation_cannot_speak_fresh_context_recovers_text(self):
        record = await self.run_slot(None, random_value=0.99)
        self.assertEqual(record.metadata["output_kind"], "text")
        self.assertEqual(record.metadata["generation_engine"], "personality_v1")
        self.assertEqual(record.metadata["personality_personal"], False)
        self.service._send_text_fallback.assert_awaited_once()
        self.service._send_media_candidate.assert_not_awaited()

    async def test_no_context_leaves_media_alive_when_text_unavailable(self):
        self.service._culture_memory_snapshot.return_value = CultureMemorySnapshot(
            recent_events=[event(text="Привет"), picture()],
            historical_windows=[],
            counts=MemoryCounts(total=20, text=15, photo=1),
            generation=self.snapshot.generation,
        )
        record = await self.run_slot(None, random_value=0.99)
        self.assertEqual(record.metadata["output_kind"], "media")
        self.service._send_media_candidate.assert_awaited_once()
        self.service._send_text_fallback.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()

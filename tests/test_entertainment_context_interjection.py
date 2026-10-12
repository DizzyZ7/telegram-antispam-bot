"""Fosgen spontaneous dialogue interjections stay relevant and within budget."""
from __future__ import annotations

import random
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from entertainment.context_interjection import (
    ContextInterjection, authored_interjections, choose_context_interjection,
)
from entertainment.models import (
    EntertainmentActionRecord, EntertainmentActionType,
    EntertainmentSettings, MemoryCounts, MemoryEvent, MemoryEventType,
)
from entertainment.moderation_safety import is_safe_entertainment_reply
from entertainment.autonomy import ActionCandidate
from entertainment import EntertainmentService
from entertainment.context import ActivitySnapshot


NOW = 50_000
CHAT = -100100
TOPIC = 1200


def evt(msg_id: int, user: int, text: str, *, ago: int = 20,
        topic: int = TOPIC, bot: bool = False) -> MemoryEvent:
    return MemoryEvent(
        id=msg_id, message_id=msg_id, chat_id=CHAT, topic_id=topic,
        user_id=user, event_type=MemoryEventType.TEXT,
        text=text, created_at=NOW-ago, sender_is_bot=bot,
    )


def choose(events, *, history=(), outputs=()):
    return choose_context_interjection(
        events, now=NOW, recent_actions=history, recent_outputs=outputs,
        rng=random.Random(42),
    )


class InterjectionSafetyTests(unittest.TestCase):
    def test_all_handwritten_lines_are_safe(self):
        lines = authored_interjections()
        self.assertGreaterEqual(len(lines), 30)
        for line in lines:
            with self.subTest(line=line):
                self.assertTrue(is_safe_entertainment_reply(line))

    def test_two_people_discussing_same_topic_enable_context(self):
        result = choose([
            evt(1, 1, "Снова пишу главу про персонажа", ago=70),
            evt(2, 2, "Что же будет в конце этой главы", ago=20),
        ])
        self.assertIsNotNone(result)
        self.assertEqual(result.scene, "writing")

    def test_different_topics_do_not_falsely_count_two_people(self):
        cases = (
            [evt(1, 1, "Дописал главу", ago=70),
             evt(2, 1, "Персонаж главу закончил", ago=10)],
            [evt(1, 1, "Пишу книгу", ago=70),
             evt(2, 2, "Сервер не запускается", ago=10)],
            [evt(1, 1, "Пишу главу", ago=70),
             evt(2, 2, "Пишу главу", ago=10, topic=TOPIC+1)],
            [evt(1, 1, "Пишу главу", ago=500),
             evt(2, 2, "Пишу главу", ago=10)],
            [evt(1, 1, "Пишу главу", ago=70),
             evt(2, 2, "Пишу главу", ago=100, bot=True)],
            [evt(1, 1, "Пишу главу", ago=110),
             evt(2, 2, "Пишу главу", ago=100)],
        )
        # Last case is stale: freshest event is over 90s old.
        for events in cases:
            with self.subTest(events=events):
                self.assertIsNone(choose(events))

    def test_distress_or_hostility_silences_interjection(self):
        for text in ("Меня травля в чате достала", "Мне страшно, я в больнице"):
            events = [
                evt(1, 1, "Редактор дописывает главу", ago=50),
                evt(2, 2, text, ago=20),
            ]
            self.assertIsNone(choose(events))

    def test_recent_interjection_and_exhausted_lines_suppress(self):
        events = [evt(1, 1, "Пишу книгу"), evt(2, 2, "Читаю главу")]
        action = EntertainmentActionRecord(
            id=3, chat_id=CHAT, topic_id=TOPIC,
            action_type=EntertainmentActionType.REMIXED_PHRASE,
            created_at=NOW - 25, trigger_message_id=800,
            metadata={"source":"context_interjection_v1"},
        )
        self.assertIsNone(choose(events, history=[action]))
        lines = [line for line in authored_interjections() if line]
        self.assertIsNone(choose(events, outputs=lines))


class InterjectionRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_approved_slot_prefers_original_context_text_and_logs_scene(self):
        events = [
            evt(1, 1, "Снова пишу главу", ago=80),
            evt(2, 2, "Эта глава прямо как роман", ago=20),
        ]
        store = SimpleNamespace(
            get_settings=AsyncMock(return_value=EntertainmentSettings()),
            message_count=AsyncMock(return_value=120),
            activity_snapshot=AsyncMock(return_value=ActivitySnapshot(
                chat_id=CHAT, topic_id=TOPIC,
                messages_1m=2, messages_5m=6, messages_previous_5m=2,
                messages_15m=7, active_users_5m=2,
                seconds_since_human=5.0,
            )),
            recent_actions=AsyncMock(return_value=[]),
            human_messages_since=AsyncMock(return_value=10),
            recent_events=AsyncMock(return_value=events),
            sample_event_windows=AsyncMock(return_value=[]),
            memory_counts=AsyncMock(return_value=MemoryCounts(total=120, text=120)),
            recent_texts=AsyncMock(return_value=["Пишу главу"]*35),
            recent_messages=AsyncMock(return_value=["Пишу главу"]*35),
            record_action=AsyncMock(return_value=55),
        )
        msg = SimpleNamespace(
            chat=SimpleNamespace(id=CHAT,type="supergroup"),
            from_user=SimpleNamespace(id=2,is_bot=False),
            text="Эта глава прямо как роман", caption=None,
            message_thread_id=TOPIC,message_id=2,
            reply_to_message=None,reply=AsyncMock(),
        )
        svc = EntertainmentService(
            SimpleNamespace(bot=SimpleNamespace(id=999,send_message=AsyncMock())),
            store, {CHAT}, rng=random.Random(2), now_fn=lambda: NOW,
        )
        selected = ActionCandidate(
            action_type=EntertainmentActionType.REMIXED_PHRASE,
            relevance=0.99, novelty=0.9, annoyance_cost=0.01,
        )
        with (
            patch("entertainment.culture_service.select_action", return_value=selected),
            patch("entertainment.culture_service.select_media_candidate", return_value=None),
            patch.object(svc, "_generate_culture_text", return_value=(None, None)),
            patch.object(svc.rng, "random", return_value=0.0),
        ):
            record = await svc.evaluate_topic(msg)
        self.assertIsNotNone(record)
        self.assertEqual(record.metadata["source"], "context_interjection_v1")
        self.assertEqual(record.metadata["context_interjection_scene"], "writing")
        self.assertEqual(record.metadata["generation_engine"], "context_interjection_v1")
        self.assertTrue(is_safe_entertainment_reply(record.metadata["output"]))
        msg.reply.assert_awaited_once()
        self.assertNotIn("Пишу главу", record.metadata["output"])
        self.assertNotIn("context_messages", record.metadata)


if __name__ == "__main__":
    unittest.main()

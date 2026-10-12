"""Fosgen text recovery beyond literary-only conversations."""
from __future__ import annotations

import random
import unittest

from entertainment.banter import (
    choose_banter_line,
    detect_banter_topic,
    recent_banter_context,
)
from entertainment.models import MemoryEvent, MemoryEventType

NOW = 100_000
CHAT = -1002619489118
TOPIC = 4321


def event(
    text: str,
    *,
    created_at: int = NOW - 10,
    topic: int = TOPIC,
    chat: int = CHAT,
    kind: MemoryEventType = MemoryEventType.TEXT,
    sender_is_bot: bool = False,
    forwarded: bool = False,
    command: bool = False,
) -> MemoryEvent:
    return MemoryEvent(
        id=1, chat_id=chat, topic_id=topic, message_id=3,
        user_id=77, event_type=kind, created_at=created_at,
        text=text if kind is not MemoryEventType.PHOTO else None,
        caption=text if kind is MemoryEventType.PHOTO else None,
        sender_is_bot=sender_is_bot, is_forwarded=forwarded, is_command=command,
    )


class BanterRecoveryUnitTests(unittest.TestCase):
    def test_five_general_conversation_topics(self):
        examples = {
            "Ждем ответа уже третий день": "waiting",
            "Ура, наконец все заработало": "celebration",
            "Эта ошибка уже второй раз": "technical",
            "Ахахаха я не могу": "laughter",
            "Выбираю между двумя вариантами": "choice",
        }
        for message, topic in examples.items():
            with self.subTest(topic=topic):
                self.assertEqual(detect_banter_topic(message), topic)

    def test_unrelated_commands_links_and_short_text_not_used(self):
        for message in (
            "", "Окей", "/start ура победа",
            "https://example.org/ошибка", "www.example.org готово",
            "t.me/foo ахахах", "Вечером увидимся",
            "что-то " * 80,
        ):
            with self.subTest(message=message[:30]):
                self.assertIsNone(detect_banter_topic(message))

    def test_only_fresh_human_same_topic_text_is_eligible(self):
        self.assertEqual(
            recent_banter_context([event("Ура, наконец все заработало")], now=NOW),
            "Ура, наконец все заработало",
        )
        for item in (
            event("Ура, все готово", created_at=NOW - 1201),
            event("Ура, все готово", created_at=NOW + 1),
            event("Ура, все готово", sender_is_bot=True),
            event("Ура, все готово", forwarded=True),
            event("Ура, все готово", command=True),
        ):
            self.assertIsNone(recent_banter_context([item], now=NOW))
        self.assertEqual(
            recent_banter_context([
                event("Привет"),
                event("Ура, все готово", topic=TOPIC + 1),
            ], now=NOW),
            None,
        )

    def test_a_recent_caption_can_supply_context_without_copying_it(self):
        recent = "Выбираю между двумя вариантами обложки"
        self.assertEqual(
            recent_banter_context([event(recent, kind=MemoryEventType.PHOTO)], now=NOW),
            recent,
        )
        chosen = choose_banter_line(
            trigger_text=recent, recent_outputs=[], rng=random.Random(7),
        )
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen[1], "choice")
        self.assertNotIn(recent, chosen[0])

    def test_last_non_trigger_message_does_not_erase_recent_relevant_context(self):
        events = [
            event("Ура, все готово", created_at=NOW - 12),
            event("Ну хорошо", created_at=NOW - 4),
        ]
        self.assertEqual(recent_banter_context(events, now=NOW), "Ура, все готово")

    def test_deduplicates_and_returns_none_when_catalog_exhausted(self):
        first = choose_banter_line(
            trigger_text="Ну наконец все получилось",
            recent_outputs=[],
            rng=random.Random(1),
        )
        self.assertIsNotNone(first)
        seen = [first[0]]
        second = choose_banter_line(
            trigger_text="Ну наконец все получилось",
            recent_outputs=seen,
            rng=random.Random(1),
        )
        self.assertIsNotNone(second)
        self.assertNotEqual(first[0], second[0])
        outputs = []
        for _ in range(6):
            current = choose_banter_line(
                trigger_text="Ну наконец все получилось",
                recent_outputs=outputs,
                rng=random.Random(1),
            )
            self.assertIsNotNone(current)
            outputs.append(current[0])
        self.assertIsNone(choose_banter_line(
            trigger_text="Ну наконец все получилось",
            recent_outputs=outputs,
            rng=random.Random(1),
        ))


if __name__ == "__main__":
    unittest.main()

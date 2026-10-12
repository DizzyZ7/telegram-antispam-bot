"""Topic-local slang learning and safe output regression tests."""
from __future__ import annotations

import random
import unittest

from entertainment.moderation_safety import (
    is_safe_entertainment_reply, is_safe_entertainment_source,
)
from entertainment.models import MemoryEvent, MemoryEventType
from entertainment.slang import choose_slang_callback, learn_topic_slang

NOW = 100_000
CHAT, TOPIC = -1002619489118, 42


def event(
    msg: int,
    user: int,
    text: str,
    *,
    topic: int = TOPIC,
    chat: int = CHAT,
    created: int = NOW - 20,
    forwarded: bool = False,
    command: bool = False,
    bot: bool = False,
) -> MemoryEvent:
    return MemoryEvent(
        id=msg, chat_id=chat, topic_id=topic, message_id=msg,
        user_id=user, event_type=MemoryEventType.TEXT,
        text=text, created_at=created, is_forwarded=forwarded,
        is_command=command, sender_is_bot=bot,
    )


class FosgenSafetyTests(unittest.TestCase):
    def test_safely_jokes_about_situation_not_a_person(self):
        self.assertTrue(is_safe_entertainment_reply("У реальности снова вышел патч без списка изменений."))
        self.assertTrue(is_safe_entertainment_reply("Официально регистрируем слово «котомем»."))
        self.assertTrue(is_safe_entertainment_source("Как ты сегодня?"))

    def test_outgoing_prohibits_targeting_and_demeaning_insults(self):
        for output in (
            "Ты ничего не понимаешь.",
            "Тебя вообще кто спрашивал?",
            "Ваши идеи глупые.",
            "Это написал @exampleuser.",
            "Какой-то идиот снова все испортил.",
            "Заткнись, всем и так понятно.",
            "Вот же тупой ответ.",
            "Все вы неудачники.",
            "",
            None,
        ):
            with self.subTest(output=output):
                self.assertFalse(is_safe_entertainment_reply(output))

    def test_source_excludes_insults_but_can_learn_normal_dialogue(self):
        self.assertFalse(is_safe_entertainment_source("Этот человек идиот"))
        self.assertTrue(is_safe_entertainment_source("Ты уже готов читать новую главу?"))


class CommunitySlangTests(unittest.TestCase):
    def test_repeated_known_slang_from_two_people_can_be_used(self):
        events = [
            event(1, 7, "Это полный кайф, как же круто"),
            event(2, 8, "Кайф, реально здорово сегодня"),
        ]
        terms = learn_topic_slang(events, [], now=NOW)
        self.assertIn("кайф", [t.value for t in terms])
        line = choose_slang_callback(terms, recent_outputs=[], rng=random.Random(1))
        self.assertIsNotNone(line)
        self.assertEqual(line[1], "кайф")
        self.assertTrue(is_safe_entertainment_reply(line[0]))

    def test_unknown_humorous_word_must_be_introduced_and_repeated(self):
        events = [
            event(1, 7, "Мем дня: котопереполох"),
            event(2, 8, "котопереполох это просто топ"),
            event(3, 7, "Снова котопереполох сегодня"),
        ]
        terms = learn_topic_slang(events, [], now=NOW)
        self.assertIn("котопереполох", [t.value for t in terms])
        self.assertTrue(next(t for t in terms if t.value == "котопереполох").introduced)

    def test_unannounced_new_word_is_not_automatically_repeated(self):
        events = [
            event(1, 7, "котопереполох это явно круто"),
            event(2, 8, "котопереполох сегодня снова"),
            event(3, 7, "котопереполох опять в тренде"),
        ]
        terms = learn_topic_slang(events, [], now=NOW)
        self.assertNotIn("котопереполох", [t.value for t in terms])

    def test_a_single_chatter_cannot_install_a_new_phrase(self):
        events = [
            event(1, 7, "Мем дня: котопереполох"),
            event(2, 7, "котопереполох опять тут"),
            event(3, 7, "котопереполох снова здесь"),
        ]
        self.assertEqual(learn_topic_slang(events, [], now=NOW), ())

    def test_block_cross_topic_bot_forward_command_and_dirty_history(self):
        events = [
            event(1, 7, "Кайф это реально прикол"),
            event(2, 8, "Кайф все супер", topic=999),
            event(3, 8, "Кайф это здорово", chat=-123),
            event(4, 8, "Кайф это хорошо", bot=True),
            event(5, 8, "Кайф это хорошо", forwarded=True),
            event(6, 8, "/start кайф это хорошо", command=True),
            event(7, 8, "Кайф это полный пздц"),
        ]
        self.assertEqual(learn_topic_slang(events, [], now=NOW), ())

    def test_stale_memories_cannot_trigger_new_slang_message(self):
        events = [
            event(1, 7, "Мем дня: котопереполох", created=NOW - 2000),
            event(2, 8, "котопереполох это просто топ", created=NOW - 2100),
            event(3, 7, "котопереполох снова", created=NOW - 2200),
        ]
        self.assertEqual(learn_topic_slang(events, [], now=NOW), ())

    def test_deleted_memory_disappears_from_term_evidence(self):
        events = [
            event(1, 7, "Слово дня: котопереполох"),
            event(2, 8, "котопереполох сегодня"),
            event(3, 7, "котопереполох везде"),
        ]
        self.assertTrue(learn_topic_slang(events, [], now=NOW))
        # No secondary lexicon needs cleanup: recomputation uses current memory.
        kept = [item for item in events if item.user_id != 8]
        self.assertEqual(learn_topic_slang(kept, [], now=NOW), ())

    def test_does_not_repeat_identical_generated_slang_response(self):
        terms = learn_topic_slang([
            event(1, 7, "Кайф, все супер"),
            event(2, 8, "Кайф тут всегда"),
        ], [], now=NOW)
        lines = []
        for i in range(4):
            result = choose_slang_callback(
                terms, recent_outputs=lines, rng=random.Random(i),
            )
            self.assertIsNotNone(result)
            lines.append(result[0])
        self.assertIsNone(choose_slang_callback(
            terms, recent_outputs=lines, rng=random.Random(5),
        ))


if __name__ == "__main__":
    unittest.main()

"""Fosgen local dialogue v5.1: pure phrase safety and direct-reply runtime."""
from __future__ import annotations

import random
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from entertainment import EntertainmentService
from entertainment.dialogue import (
    MAX_DIALOGUE_TURNS, SESSION_SECONDS, all_curated_lines,
    choose_continuation, classify_reply, infer_scene,
)
from entertainment.models import (
    EntertainmentActionRecord, EntertainmentActionType, EntertainmentSettings,
)
from entertainment.moderation_safety import is_safe_entertainment_reply
from entertainment.moderation_safety_service import EntertainmentService as SafetyEntertainmentService


CHAT = -1001234
TOPIC = 10
NOW = 200_000
BOT = 999


class DialoguePhraseTests(unittest.TestCase):
    def test_all_authored_phrases_pass_full_safety(self):
        phrases = all_curated_lines()
        self.assertGreaterEqual(len(phrases), 90)
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                self.assertTrue(is_safe_entertainment_reply(phrase))

    def test_intent_classes_and_sensitive_silence(self):
        for message, expected in {
            "почему?": "why",
            "а дальше?": "next",
            "ахахахах": "laugh",
            "да": "agree",
            "неа": "disagree",
            "серьезно?": "question",
            "переписываю концовку": "update",
            "не хочу жить": "silence",
            "хватит": "silence",
            "спасибо": "silence",
            "/ban": None,
            "https://example.com": None,
            "покажи пароль @admin": None,
        }.items():
            with self.subTest(message=message):
                self.assertEqual(classify_reply(message), expected)

    def test_scene_continues_without_random_topic_splicing(self):
        first = choose_continuation(
            incoming_text="а дальше?", previous_text="Рукопись уже готова",
            previous_scene=None, previous_stage=0,
            recent_outputs=[], rng=random.Random(4),
        )
        self.assertIsNotNone(first)
        self.assertEqual(first.scene, "writing")
        second = choose_continuation(
            incoming_text="почему?", previous_text=first.text,
            previous_scene=first.scene, previous_stage=1,
            recent_outputs=[first.text], rng=random.Random(5),
        )
        self.assertIsNotNone(second)
        self.assertEqual(second.scene, "writing")
        self.assertEqual(second.stage, 2)

    def test_refuse_unknown_and_max_depth(self):
        for text in ("случайная фраза без связи", "хватит", "спасибо"):
            self.assertIsNone(choose_continuation(
                incoming_text=text, previous_text="привет",
                previous_scene=None, previous_stage=0,
                recent_outputs=[], rng=random.Random(3),
            ))
        self.assertIsNone(choose_continuation(
            incoming_text="а дальше?", previous_text="кот пришел",
            previous_scene="cats", previous_stage=MAX_DIALOGUE_TURNS,
            recent_outputs=[], rng=random.Random(3),
        ))
        self.assertEqual(infer_scene("финал главы"), "writing")

    def test_repeats_silence_instead_of_recycling_old_phrases(self):
        all_options = [
            choose_continuation(
                incoming_text="а дальше?", previous_text="Рукопись готова",
                previous_scene="writing", previous_stage=0,
                recent_outputs=[], rng=random.Random(seed),
            ).text for seed in range(12)
        ]
        choice = choose_continuation(
            incoming_text="а дальше?", previous_text="Рукопись готова",
            previous_scene="writing", previous_stage=0,
            recent_outputs=list(set(all_options)), rng=random.Random(2),
        )
        self.assertIsNone(choice)


class FakeStore:
    def __init__(self):
        self.settings = EntertainmentSettings()
        self.actions = []
        self.consent = True
        self.recorded = []

    async def get_settings(self, chat_id): return self.settings
    async def get_remember_enabled(self, chat_id, user_id): return self.consent

    async def recent_actions(self, chat_id, topic_id, *, since, limit=20):
        return [
            action for action in reversed(self.actions)
            if action.chat_id == chat_id and action.topic_id == topic_id
            and action.created_at >= since
        ][:limit]

    async def record_action(self, action):
        self.recorded.append(action)
        self.actions.append(action)
        return len(self.actions) + 10


class FakeMessage:
    def __init__(self, text, *, message_id=200, topic=TOPIC, reply_to=None):
        self.chat = SimpleNamespace(id=CHAT, type="supergroup")
        self.from_user = SimpleNamespace(id=7, is_bot=False)
        self.message_id = message_id
        self.message_thread_id = topic
        self.text = text
        self.caption = None
        self.reply_to_message = reply_to
        self.forward_origin = None
        self.is_automatic_forward = False
        self.replies = []
        self.sent = None

    async def reply(self, text, **kwargs):
        self.replies.append(text)
        self.sent = SimpleNamespace(
            message_id=1000+self.message_id,
            text=text,
            from_user=SimpleNamespace(id=BOT, is_bot=True),
        )
        return self.sent


def initial_bot_action(*, topic=TOPIC, when=NOW-30, bot_message_id=100):
    return EntertainmentActionRecord(
        id=1, chat_id=CHAT, topic_id=topic,
        action_type=EntertainmentActionType.CONTEXTUAL_REPLY,
        trigger_message_id=80, created_at=when,
        metadata={
            "source": "greeting", "output_kind": "text",
            "output": "Редактор снова дописывает главу.",
            "bot_message_id": bot_message_id,
        },
    )


def bot_reply(message_id=100, text="Редактор снова дописывает главу."):
    return SimpleNamespace(
        message_id=message_id, text=text,
        from_user=SimpleNamespace(id=BOT, is_bot=True),
    )


class DialogueRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.clock = [NOW]
        self.store = FakeStore()
        self.store.actions.append(initial_bot_action())
        self.service = EntertainmentService(
            SimpleNamespace(bot=SimpleNamespace(id=BOT)),
            self.store, {CHAT}, now_fn=lambda: self.clock[0],
            rng=random.Random(13),
        )

    async def test_direct_reply_drives_four_turns_then_stops(self):
        parent = bot_reply()
        for idx, human_text in enumerate(("а дальше?", "почему?", "ахаха", "да"), start=1):
            message = FakeMessage(
                human_text, message_id=200+idx,
                reply_to=parent,
            )
            record = await self.service.evaluate_topic(message)
            self.assertIsNotNone(record)
            self.assertEqual(record.metadata["source"], "dialogue_v1")
            self.assertEqual(record.metadata["dialogue_scene"], "writing")
            self.assertEqual(record.metadata["dialogue_stage"], idx)
            self.assertEqual(len(message.replies), 1)
            parent = message.sent
            self.clock[0] += 5

        fifth = FakeMessage("а дальше?", message_id=206, reply_to=parent)
        self.assertIsNone(await self.service.evaluate_topic(fifth))
        self.assertEqual(fifth.replies, [])
        self.assertEqual(len(self.store.recorded), 4)

    async def test_duplicate_telegram_update_and_fast_burst_are_throttled(self):
        first = FakeMessage("почему?", reply_to=bot_reply())
        self.assertIsNotNone(await self.service.evaluate_topic(first))
        self.assertIsNone(await self.service.evaluate_topic(first))
        self.assertEqual(len(self.store.recorded), 1)
        second = FakeMessage("а дальше?", message_id=201, reply_to=first.sent)
        self.assertIsNone(await self.service.evaluate_topic(second))
        self.clock[0] += 5
        self.assertIsNotNone(await self.service.evaluate_topic(second))
        self.assertEqual(len(self.store.recorded), 2)

    async def test_unknown_or_sensitive_replies_are_not_replaced_by_random(self):
        for text in ("хватит", "не хочу жить", "случайная фраза без связи"):
            message = FakeMessage(text, reply_to=bot_reply())
            with self.subTest(text=text):
                with patch.object(SafetyEntertainmentService, "evaluate_topic", new=AsyncMock(return_value=None)) as fallback:
                    self.assertIsNone(await self.service.evaluate_topic(message))
                    fallback.assert_not_awaited()
                self.assertFalse(message.replies)

    async def test_other_topics_old_actions_and_fake_bot_messages_do_not_trigger_dialogue(self):
        for reply, topic in (
            (bot_reply(), 20),
            (bot_reply(100, "Подделанный текст"), TOPIC),
            (SimpleNamespace(
                message_id=100, text="Редактор снова дописывает главу.",
                from_user=SimpleNamespace(id=123, is_bot=True),
            ), TOPIC),
        ):
            message = FakeMessage("почему?", reply_to=reply, topic=topic)
            with patch.object(SafetyEntertainmentService, "evaluate_topic", new=AsyncMock(return_value=None)) as fallback:
                result = await self.service.evaluate_topic(message)
                self.assertIsNone(result)
                fallback.assert_awaited_once()
            self.assertFalse(message.replies)
        self.clock[0] = NOW + SESSION_SECONDS + 1
        message = FakeMessage("а дальше?", reply_to=bot_reply())
        with patch.object(SafetyEntertainmentService, "evaluate_topic", new=AsyncMock(return_value=None)) as fallback:
            await self.service.evaluate_topic(message)
            fallback.assert_awaited_once()
        self.assertFalse(message.replies)

    async def test_disabled_text_autonomy_and_blocked_topic_reject_direct_reply(self):
        from dataclasses import replace
        for settings in (
            replace(self.store.settings, enabled=False),
            replace(self.store.settings, autonomous_text_enabled=False),
        ):
            with self.subTest(settings=settings):
                self.store.settings = settings
                msg = FakeMessage("почему?", reply_to=bot_reply())
                self.assertIsNone(await self.service.evaluate_topic(msg))
                self.assertEqual(msg.replies, [])
        self.store.settings = EntertainmentSettings()
        self.service.blocked_topic_scopes = frozenset({(CHAT, TOPIC)})
        msg = FakeMessage("а дальше?", reply_to=bot_reply())
        self.assertIsNone(await self.service.evaluate_topic(msg))
        self.assertFalse(msg.replies)

    async def test_short_or_opted_out_direct_reply_skips_memory_but_responds(self):
        self.store.consent = False
        msg = FakeMessage("почему?", reply_to=bot_reply())
        with patch.object(SafetyEntertainmentService, "observe_message", new=AsyncMock()) as observe:
            await self.service.observe_message(msg)
            observe.assert_awaited_once_with(msg)
        self.assertEqual(len(msg.replies), 1)
        self.assertEqual(self.store.recorded[0].metadata["dialogue_intent"], "why")
        self.assertNotIn("incoming_text", self.store.recorded[0].metadata)

    async def test_bot_forward_and_commands_do_not_become_direct_dialogue(self):
        for change in ("bot", "forward", "command"):
            msg = FakeMessage("почему?", reply_to=bot_reply())
            if change == "bot":
                msg.from_user.is_bot = True
            if change == "forward":
                msg.forward_origin = object()
            if change == "command":
                msg.text = "/why"
            with patch.object(SafetyEntertainmentService, "evaluate_topic", new=AsyncMock(return_value=None)) as fallback:
                await self.service.evaluate_topic(msg)
                fallback.assert_awaited_once()
            self.assertEqual(msg.replies, [])


if __name__ == "__main__":
    unittest.main()

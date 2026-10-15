from __future__ import annotations

import random
import unittest

from entertainment.meme_memory import (
    LocalMeme, choose_meme_callback, could_recall_meme, rank_local_memes,
)
from entertainment.models import (
    EntertainmentActionRecord, EntertainmentActionType, MemoryEvent, MemoryEventType,
)
from entertainment.moderation_safety import is_safe_entertainment_reply

CHAT = -100123
TOPIC = 14
NOW = 50_000
BOT_TEXT = "В протокол внесены внеплановые овации."


def action(*, chat=CHAT, topic=TOPIC, at=NOW-7200, mid=222, output=BOT_TEXT):
    return EntertainmentActionRecord(
        id=1, chat_id=chat, topic_id=topic,
        action_type=EntertainmentActionType.CONTEXTUAL_REPLY,
        trigger_message_id=40, created_at=at,
        metadata={"output_kind": "text", "output": output,
                  "bot_message_id": mid, "source": "dialogue_v1"},
    )


def event(text, *, user=10, chat=CHAT, topic=TOPIC, at=NOW-7000,
          reply=222, is_bot=False, is_forwarded=False):
    return MemoryEvent(
        id=user, chat_id=chat, topic_id=topic, user_id=user,
        event_type=MemoryEventType.TEXT,
        message_id=1000+user, created_at=at,
        text=text, reply_to_message_id=reply, sender_is_bot=is_bot,
        is_forwarded=is_forwarded,
    )


class LocalMemeMemoryTests(unittest.TestCase):
    def test_multiple_real_people_react_to_original_bot_joke(self):
        events = [
            event("АХАХАХАХ", user=10),
            event("Овации внеплановые... ХАХАХ", user=11, reply=None, at=NOW-6900),
            event("АХАХ", user=10, at=NOW-6890),
        ]
        memes = rank_local_memes([action()], events, chat_id=CHAT, topic_id=TOPIC, now=NOW)
        self.assertEqual(len(memes), 1)
        self.assertIsInstance(memes[0], LocalMeme)
        self.assertEqual(memes[0].supporter_count, 2)
        self.assertEqual(memes[0].bot_text, BOT_TEXT)
        self.assertEqual(memes[0].score, 4)
        callback = choose_meme_callback(
            [action()], events, chat_id=CHAT, topic_id=TOPIC, now=NOW,
            trigger_text="Овации внеплановые, помнишь?",
            recent_outputs=[], rng=random.Random(4),
        )
        self.assertIsNotNone(callback)
        self.assertIn(BOT_TEXT, callback)
        self.assertTrue(is_safe_entertainment_reply(callback))

    def test_single_person_spamming_cannot_create_a_classic(self):
        events = [
            event("ахах", user=11, at=NOW-7100+i, reply=222)
            for i in range(9)
        ]
        self.assertEqual(
            rank_local_memes([action()], events, chat_id=CHAT, topic_id=TOPIC, now=NOW),
            (),
        )

    def test_consent_and_deleted_messages_are_not_silently_reintroduced(self):
        # The production accessor returns ONLY still-consented Culture events.
        # If one user opts out/deletes their memory, they vanish from votes.
        events = [event("АХАХ", user=10), event("АХАХ", user=11)]
        self.assertEqual(len(rank_local_memes(
            [action()], events, chat_id=CHAT, topic_id=TOPIC, now=NOW
        )), 1)
        self.assertEqual(rank_local_memes(
            [action()], events[:1], chat_id=CHAT, topic_id=TOPIC, now=NOW
        ), ())

    def test_no_cross_chat_topic_or_bot_votes_or_forwarded_messages(self):
        main = action()
        cases = (
            event("АХАХ", user=21, chat=CHAT + 2),
            event("АХАХ", user=22, topic=TOPIC + 1),
            event("АХАХ", user=23, is_bot=True),
            event("АХАХ", user=24, is_forwarded=True),
            event("АХАХ", user=25, at=NOW-10_000),
        )
        for another in cases:
            with self.subTest(another=another):
                self.assertEqual(rank_local_memes(
                    [main], [event("АХАХ", user=10), another],
                    chat_id=CHAT, topic_id=TOPIC, now=NOW,
                ), ())
        self.assertEqual(rank_local_memes(
            [action(chat=CHAT+1)], [
                event("АХАХ", user=10), event("АХАХ", user=20)
            ], chat_id=CHAT, topic_id=TOPIC, now=NOW,
        ), ())

    def test_recall_requires_actual_echo_and_cooldown_after_reactions(self):
        old = [event("АХАХ", user=10), event("Овации внеплановые АХАХА", user=11, reply=None)]
        assert rank_local_memes([action()], old, chat_id=CHAT, topic_id=TOPIC, now=NOW)
        options = dict(
            chat_id=CHAT, topic_id=TOPIC, now=NOW, recent_outputs=[],
            rng=random.Random(2),
        )
        self.assertIsNone(choose_meme_callback(
            [action()], old, trigger_text="а что насчет экзамена?", **options,
        ))
        self.assertIsNone(choose_meme_callback(
            [action()], old, trigger_text="Овации внеплановые",
            recent_outputs=[BOT_TEXT], chat_id=CHAT, topic_id=TOPIC,
            now=NOW, rng=random.Random(2),
        ))
        recent = [event("АХАХ", user=10, at=NOW-20), event("АХАХ", user=11, at=NOW-19)]
        self.assertIsNone(choose_meme_callback(
            [action(at=NOW-80)], recent,
            trigger_text="Овации внеплановые", **options,
        ))

    def test_recall_never_uses_random_human_or_unsafe_bot_text(self):
        safe = action()
        bad = action(mid=300, output="Ты бездарный и смешной.")
        events = [
            event("АХАХ", user=10),
            event("Ахах", user=11),
            event("Секретный пароль: watermelon123", user=12, reply=None),
        ]
        meme = rank_local_memes([safe, bad], events, chat_id=CHAT, topic_id=TOPIC, now=NOW)
        self.assertEqual(len(meme), 1)
        self.assertNotIn("watermelon123", meme[0].bot_text)
        self.assertFalse(could_recall_meme("пока"))
        self.assertTrue(could_recall_meme("внеплановые овации"))

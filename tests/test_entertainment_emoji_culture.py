import random
import unittest

from entertainment.culture import apply_emoji_style, build_culture_context
from entertainment.models import MemoryEvent, MemoryEventType


def event(
    message_id: int,
    *,
    text: str | None = None,
    event_type: MemoryEventType = MemoryEventType.TEXT,
    sticker_emoji: str | None = None,
    topic_id: int = 10,
    created_at: int = 1000,
) -> MemoryEvent:
    return MemoryEvent(
        id=message_id,
        chat_id=-1001,
        topic_id=topic_id,
        message_id=message_id,
        user_id=7,
        event_type=event_type,
        created_at=created_at,
        text=text,
        sticker_emoji=sticker_emoji,
    )


class AlwaysStyleRandom(random.Random):
    def random(self) -> float:
        return 0.0

    def choice(self, seq):
        return seq[0]


class NeverStyleRandom(random.Random):
    def random(self) -> float:
        return 0.99


class EmojiCultureTests(unittest.TestCase):
    def test_contextual_emoji_prefers_same_topic_terms(self):
        recent = [
            event(1, text="автобус ночью едет 😂", created_at=1000),
            event(2, text="котик сладко спит 😼", created_at=1010),
            event(3, text="автобус снова на вокзале 😂", created_at=1020),
        ]
        context = build_culture_context(recent, [], trigger_text="где наш автобус")

        styled, signature = apply_emoji_style(
            "автобус скоро приедет",
            context,
            recent_signatures=set(),
            rng=AlwaysStyleRandom(),
        )

        self.assertIn("😂", styled)
        self.assertNotIn("😼", styled)
        self.assertEqual(signature, "😂")

    def test_style_is_optional_and_never_mandatory(self):
        context = build_culture_context(
            [event(1, text="автобус едет 😂")],
            [],
            trigger_text="автобус",
        )

        styled, signature = apply_emoji_style(
            "автобус приехал",
            context,
            recent_signatures=set(),
            rng=NeverStyleRandom(),
        )

        self.assertEqual(styled, "автобус приехал")
        self.assertIsNone(signature)

    def test_style_adds_at_most_two_emoji(self):
        context = build_culture_context(
            [
                event(1, text="поезд автобус вокзал 😂🔥"),
                event(2, text="поезд автобус опять 😭✨"),
            ],
            [],
            trigger_text="поезд автобус",
        )

        styled, signature = apply_emoji_style(
            "поезд уже рядом",
            context,
            recent_signatures=set(),
            rng=AlwaysStyleRandom(),
        )

        self.assertIsNotNone(signature)
        self.assertLessEqual(len(signature), 2)
        self.assertTrue(styled.endswith(signature))

    def test_recent_signature_is_suppressed_when_alternative_exists(self):
        context = build_culture_context(
            [
                event(1, text="автобус приехал 😂"),
                event(2, text="автобус уехал 🔥"),
            ],
            [],
            trigger_text="автобус",
        )

        styled, signature = apply_emoji_style(
            "автобус снова тут",
            context,
            recent_signatures={"😂"},
            rng=AlwaysStyleRandom(),
        )

        self.assertNotEqual(signature, "😂")
        self.assertIn("🔥", styled)

    def test_sticker_emoji_contributes_weakly(self):
        context = build_culture_context(
            [
                event(1, text="котик пришел 😼"),
                event(
                    2,
                    event_type=MemoryEventType.STICKER,
                    text=None,
                    sticker_emoji="😼",
                    created_at=1010,
                ),
            ],
            [],
            trigger_text="котик",
        )

        self.assertIn("😼", context.emoji_candidates)

    def test_context_builder_discards_events_from_another_topic(self):
        context = build_culture_context(
            [
                event(1, text="автобус едет 😂", topic_id=10),
                event(2, text="чужая тема 😼", topic_id=99),
            ],
            [],
            trigger_text="автобус",
        )

        self.assertIn("😂", context.emoji_candidates)
        self.assertNotIn("😼", context.emoji_candidates)
        self.assertNotIn("чужая тема 😼", context.source_messages)


if __name__ == "__main__":
    unittest.main()

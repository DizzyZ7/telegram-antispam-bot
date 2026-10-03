from __future__ import annotations

import unittest
from types import SimpleNamespace

from entertainment.memory import classify_memory_event
from entertainment.models import MemoryEventType


def message(text: str, *, user_id: int = 7, is_bot: bool = False):
    return SimpleNamespace(
        message_id=101,
        from_user=SimpleNamespace(id=user_id, is_bot=is_bot),
        text=text,
        caption=None,
        sticker=None,
        photo=None,
        animation=None,
        reply_to_message=None,
        forward_origin=None,
        forward_date=None,
        forward_from=None,
        forward_sender_name=None,
        is_automatic_forward=False,
    )


class CultureMemoryProvenanceTests(unittest.TestCase):
    def classify(self, text: str, *, is_bot: bool = False):
        event = classify_memory_event(
            message(text, is_bot=is_bot),
            chat_id=-1001,
            topic_id=44,
            created_at=123,
        )
        self.assertIsNotNone(event)
        return event

    def test_telegram_style_commands_are_marked_as_commands(self):
        for text in ("/spawn", "/spawn boss", "/spawn@RaidBot boss", "   /roll@DiceBot 20"):
            with self.subTest(text=text):
                event = self.classify(text)
                self.assertEqual(event.event_type, MemoryEventType.TEXT)
                self.assertTrue(event.is_command)

    def test_normal_text_is_not_marked_as_command(self):
        event = self.classify("spawn boss")
        self.assertFalse(event.is_command)

    def test_sender_bot_provenance_is_preserved(self):
        bot_event = self.classify("raid spawned", is_bot=True)
        human_event = self.classify("raid spawned", is_bot=False)
        self.assertTrue(bot_event.sender_is_bot)
        self.assertFalse(human_event.sender_is_bot)


if __name__ == "__main__":
    unittest.main()

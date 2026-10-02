import unittest
from dataclasses import FrozenInstanceError

from entertainment.models import MemoryCounts, MemoryEvent, MemoryEventType


class MemoryModelTests(unittest.TestCase):
    def test_event_type_values_are_stable(self):
        self.assertEqual(
            [item.value for item in MemoryEventType],
            ["text", "emoji", "sticker", "photo", "animation"],
        )

    def test_memory_event_is_immutable_and_keeps_metadata(self):
        event = MemoryEvent(
            id=None,
            chat_id=-1001,
            topic_id=77,
            message_id=501,
            user_id=42,
            event_type=MemoryEventType.STICKER,
            text=None,
            caption=None,
            reply_to_message_id=499,
            file_id="file-id",
            file_unique_id="unique-id",
            sticker_emoji="😼",
            sticker_set_name="cats",
            media_width=512,
            media_height=512,
            media_duration=None,
            is_forwarded=False,
            legacy_source_id=None,
            metadata={"animated": True},
            created_at=123456,
        )
        self.assertEqual(event.topic_id, 77)
        self.assertEqual(event.metadata, {"animated": True})
        with self.assertRaises(FrozenInstanceError):
            event.user_id = 7

    def test_memory_counts_exposes_all_supported_types(self):
        counts = MemoryCounts(total=9, text=3, emoji=2, sticker=1, photo=2, animation=1)
        self.assertEqual(
            (counts.total, counts.text, counts.emoji, counts.sticker, counts.photo, counts.animation),
            (9, 3, 2, 1, 2, 1),
        )


if __name__ == "__main__":
    unittest.main()

import unittest
from types import SimpleNamespace

from entertainment.memory import classify_memory_event
from entertainment.models import MemoryEventType


def obj(**kwargs):
    return SimpleNamespace(**kwargs)


def message(**overrides):
    base = dict(message_id=100, text=None, caption=None, sticker=None, photo=None, animation=None,
                reply_to_message=None, forward_origin=None, from_user=obj(id=55))
    base.update(overrides)
    return obj(**base)


class MemoryClassifierTests(unittest.TestCase):
    def classify(self, value):
        return classify_memory_event(value, chat_id=-1001, topic_id=8, created_at=999)

    def test_text_and_emoji_only(self):
        text = self.classify(message(text="Едем на вокзал 😼"))
        emoji = self.classify(message(text="😂🔥"))
        self.assertEqual(text.event_type, MemoryEventType.TEXT)
        self.assertEqual(text.text, "Едем на вокзал 😼")
        self.assertEqual(emoji.event_type, MemoryEventType.EMOJI)
        self.assertEqual(emoji.text, "😂🔥")

    def test_sticker_metadata_and_reply(self):
        sticker = obj(file_id="sticker-file", file_unique_id="sticker-unique", emoji="😼",
                      set_name="cats", width=512, height=480, is_animated=True, is_video=False)
        event = self.classify(message(sticker=sticker, reply_to_message=obj(message_id=90)))
        self.assertEqual(event.event_type, MemoryEventType.STICKER)
        self.assertEqual(event.file_unique_id, "sticker-unique")
        self.assertEqual(event.reply_to_message_id, 90)
        self.assertTrue(event.metadata["animated"])

    def test_photo_uses_largest_size(self):
        photos = [
            obj(file_id="small", file_unique_id="u1", width=90, height=90, file_size=100),
            obj(file_id="large", file_unique_id="u2", width=1280, height=720, file_size=2000),
        ]
        event = self.classify(message(caption="вот это мем", photo=photos))
        self.assertEqual(event.event_type, MemoryEventType.PHOTO)
        self.assertEqual(event.file_id, "large")
        self.assertEqual(event.caption, "вот это мем")

    def test_animation_and_forward_marker(self):
        animation = obj(file_id="gif-file", file_unique_id="gif-unique", width=640, height=360, duration=4)
        event = self.classify(message(animation=animation, forward_origin=obj(kind="user")))
        self.assertEqual(event.event_type, MemoryEventType.ANIMATION)
        self.assertEqual(event.media_duration, 4)
        self.assertTrue(event.is_forwarded)

    def test_incomplete_media_is_ignored(self):
        self.assertIsNone(self.classify(message(sticker=obj(file_id=None, file_unique_id=None))))
        self.assertIsNone(self.classify(message(photo=[obj(file_id=None, file_unique_id=None, width=1, height=1)])))


if __name__ == "__main__":
    unittest.main()

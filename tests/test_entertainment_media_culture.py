from __future__ import annotations

import unittest

from entertainment.models import (
    EntertainmentActionRecord,
    EntertainmentActionType,
    MemoryEvent,
    MemoryEventType,
)
from entertainment.media_culture import select_media_candidate


def event(
    message_id: int,
    event_type: MemoryEventType,
    *,
    text: str | None = None,
    caption: str | None = None,
    file_id: str | None = None,
    file_unique_id: str | None = None,
    created_at: int = 1000,
    user_id: int = 7,
    topic_id: int = 10,
    reply_to_message_id: int | None = None,
    sender_is_bot: bool = False,
    is_forwarded: bool = False,
    sticker_emoji: str | None = None,
) -> MemoryEvent:
    return MemoryEvent(
        id=message_id,
        chat_id=-1001,
        topic_id=topic_id,
        message_id=message_id,
        user_id=user_id,
        event_type=event_type,
        created_at=created_at,
        text=text,
        caption=caption,
        file_id=file_id,
        file_unique_id=file_unique_id,
        reply_to_message_id=reply_to_message_id,
        sender_is_bot=sender_is_bot,
        is_forwarded=is_forwarded,
        sticker_emoji=sticker_emoji,
    )


def media(
    message_id: int,
    kind: MemoryEventType,
    unique: str,
    *,
    caption: str | None = None,
    created_at: int = 1000,
    topic_id: int = 10,
    sender_is_bot: bool = False,
    is_forwarded: bool = False,
    sticker_emoji: str | None = None,
) -> MemoryEvent:
    return event(
        message_id,
        kind,
        caption=caption,
        file_id=f"file-{unique}",
        file_unique_id=unique,
        created_at=created_at,
        topic_id=topic_id,
        sender_is_bot=sender_is_bot,
        is_forwarded=is_forwarded,
        sticker_emoji=sticker_emoji,
    )


def callback(unique: str, created_at: int) -> EntertainmentActionRecord:
    return EntertainmentActionRecord(
        id=1,
        chat_id=-1001,
        topic_id=10,
        action_type=EntertainmentActionType.MEMORY_CALLBACK,
        trigger_message_id=None,
        created_at=created_at,
        metadata={"media_file_unique_id": unique},
    )


class MediaCultureTests(unittest.TestCase):
    NOW = 50_000

    def select(self, recent, historical=(), *, context=("автобус опоздал на вокзал",), actions=(), count=100):
        return select_media_candidate(
            recent,
            historical,
            context_messages=context,
            recent_actions=actions,
            now=self.NOW,
            textual_event_count=count,
            bootstrap_threshold=10_000,
            repeat_cooldown_seconds=21_600,
        )

    def test_contextual_sticker_wins_over_unrelated_media(self) -> None:
        recent = [
            event(1, MemoryEventType.TEXT, text="автобус снова опоздал на вокзал", created_at=1000),
            media(2, MemoryEventType.STICKER, "bus-sticker", created_at=1010, sticker_emoji="😂"),
            event(3, MemoryEventType.TEXT, text="котик спит дома", created_at=2000),
            media(4, MemoryEventType.ANIMATION, "cat-gif", caption="сонный котик", created_at=2010),
        ]
        candidate = self.select(recent)
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.event.file_unique_id, "bus-sticker")
        self.assertEqual(candidate.source_class, "human")

    def test_animation_with_strong_context_can_pass_medium_threshold(self) -> None:
        recent = [
            event(1, MemoryEventType.TEXT, text="рейд босс появился легендарный", created_at=1000),
            media(2, MemoryEventType.ANIMATION, "raid-gif", caption="рейд босс легендарный", created_at=1010),
        ]
        candidate = self.select(recent, context=("рейд босс появился легендарный",))
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.event.file_unique_id, "raid-gif")

    def test_photo_needs_strong_context_not_generic_overlap(self) -> None:
        weak = [
            event(1, MemoryEventType.TEXT, text="автобус люди день город", created_at=1000),
            media(2, MemoryEventType.PHOTO, "weak-photo", caption="обычное фото города", created_at=1010),
        ]
        self.assertIsNone(self.select(weak, context=("автобус опоздал на вокзал",)))

        strong = [
            event(10, MemoryEventType.TEXT, text="автобус опоздал на вокзал ночью", created_at=3000),
            media(11, MemoryEventType.PHOTO, "strong-photo", caption="автобус опоздал на вокзал ночью", created_at=3010),
        ]
        candidate = self.select(strong, context=("автобус опоздал на вокзал ночью",))
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.event.file_unique_id, "strong-photo")

    def test_forwarded_or_identifierless_media_is_never_selected(self) -> None:
        recent = [
            event(1, MemoryEventType.TEXT, text="автобус опоздал на вокзал", created_at=1000),
            media(2, MemoryEventType.STICKER, "forwarded", created_at=1010, is_forwarded=True),
            event(
                3,
                MemoryEventType.STICKER,
                created_at=1020,
                file_id="has-file-but-no-unique",
            ),
        ]
        self.assertIsNone(self.select(recent))

    def test_wrong_topic_media_is_ignored(self) -> None:
        recent = [
            event(1, MemoryEventType.TEXT, text="автобус опоздал на вокзал", created_at=1000),
            media(2, MemoryEventType.STICKER, "wrong-topic", created_at=1010, topic_id=99),
        ]
        self.assertIsNone(self.select(recent))

    def test_recent_candidate_beats_equally_relevant_historical_candidate(self) -> None:
        recent = [
            event(1, MemoryEventType.TEXT, text="автобус опоздал на вокзал", created_at=5000),
            media(2, MemoryEventType.STICKER, "recent", caption="автобус опоздал на вокзал", created_at=5010),
        ]
        historical = [[
            event(10, MemoryEventType.TEXT, text="автобус опоздал на вокзал", created_at=1000),
            media(11, MemoryEventType.STICKER, "old", caption="автобус опоздал на вокзал", created_at=1010),
        ]]
        candidate = self.select(recent, historical)
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.event.file_unique_id, "recent")

    def test_other_bot_media_is_reduced_after_bootstrap(self) -> None:
        recent = [
            event(1, MemoryEventType.TEXT, text="автобус опоздал на вокзал", created_at=1000),
            media(
                2,
                MemoryEventType.STICKER,
                "bot-sticker",
                caption="автобус опоздал на вокзал",
                created_at=1010,
                sender_is_bot=True,
            ),
        ]
        before = self.select(recent, count=9_999)
        after = self.select(recent, count=10_000)
        self.assertIsNotNone(before)
        self.assertIsNone(after)

    def test_six_hour_repeat_cooldown_uses_file_unique_id(self) -> None:
        recent = [
            event(1, MemoryEventType.TEXT, text="автобус опоздал на вокзал", created_at=1000),
            media(2, MemoryEventType.STICKER, "repeat-me", caption="автобус опоздал на вокзал", created_at=1010),
        ]
        blocked = self.select(recent, actions=(callback("repeat-me", self.NOW - 100),))
        allowed = self.select(recent, actions=(callback("repeat-me", self.NOW - 21_601),))
        self.assertIsNone(blocked)
        self.assertIsNotNone(allowed)


if __name__ == "__main__":
    unittest.main()

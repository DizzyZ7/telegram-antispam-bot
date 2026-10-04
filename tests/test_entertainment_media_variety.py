from __future__ import annotations

import random
import unittest

from entertainment.media_culture import select_media_candidate
from entertainment.models import (
    EntertainmentActionRecord,
    EntertainmentActionType,
    MemoryEvent,
    MemoryEventType,
)


CHAT_ID = -1001
TOPIC_ID = 10
NOW = 50_000


def text_event(message_id: int, text: str, created_at: int) -> MemoryEvent:
    return MemoryEvent(
        id=message_id,
        chat_id=CHAT_ID,
        topic_id=TOPIC_ID,
        message_id=message_id,
        user_id=7,
        event_type=MemoryEventType.TEXT,
        created_at=created_at,
        text=text,
    )


def sticker(message_id: int, unique: str, created_at: int) -> MemoryEvent:
    return MemoryEvent(
        id=message_id,
        chat_id=CHAT_ID,
        topic_id=TOPIC_ID,
        message_id=message_id,
        user_id=7,
        event_type=MemoryEventType.STICKER,
        created_at=created_at,
        caption="редактор закончил главу рукописи",
        file_id=f"file-{unique}",
        file_unique_id=unique,
    )


def callback(unique: str, created_at: int) -> EntertainmentActionRecord:
    return EntertainmentActionRecord(
        id=1,
        chat_id=CHAT_ID,
        topic_id=TOPIC_ID,
        action_type=EntertainmentActionType.MEMORY_CALLBACK,
        trigger_message_id=None,
        created_at=created_at,
        metadata={"media_file_unique_id": unique},
    )


def select(events, *, rng: random.Random, actions=()):
    return select_media_candidate(
        events,
        (),
        context_messages=("редактор закончил главу рукописи",),
        recent_actions=actions,
        now=NOW,
        textual_event_count=100,
        bootstrap_threshold=10_000,
        repeat_cooldown_seconds=21_600,
        rng=rng,
    )


def select_live(events, *, now: int):
    return select_media_candidate(
        events,
        (),
        context_messages=("редактор закончил главу рукописи",),
        recent_actions=(),
        now=now,
        textual_event_count=100,
        bootstrap_threshold=10_000,
        repeat_cooldown_seconds=21_600,
    )


class MediaVarietyTests(unittest.TestCase):
    def test_different_rng_seeds_can_pick_different_relevant_stickers(self) -> None:
        events = [
            text_event(1, "редактор закончил главу рукописи", 1000),
            sticker(2, "sticker-a", 1010),
            text_event(3, "редактор закончил главу рукописи", 1020),
            sticker(4, "sticker-b", 1030),
        ]

        first = select(events, rng=random.Random(1))
        second = select(events, rng=random.Random(2))

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(
            {first.event.file_unique_id, second.event.file_unique_id},
            {"sticker-a", "sticker-b"},
        )

    def test_live_selection_varies_over_time_for_equally_relevant_stickers(self) -> None:
        events = [
            text_event(1, "редактор закончил главу рукописи", 1000),
            sticker(2, "sticker-a", 1010),
            text_event(3, "редактор закончил главу рукописи", 1020),
            sticker(4, "sticker-b", 1030),
        ]

        picked = {
            select_live(events, now=NOW + offset).event.file_unique_id
            for offset in range(16)
        }

        self.assertEqual(picked, {"sticker-a", "sticker-b"})

    def test_sticker_used_an_hour_ago_is_reusable_instead_of_hard_blocked_for_six_hours(self) -> None:
        events = [
            text_event(1, "редактор закончил главу рукописи", 1000),
            sticker(2, "reusable", 1010),
        ]
        candidate = select(
            events,
            rng=random.Random(3),
            actions=(callback("reusable", NOW - 3600),),
        )

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.event.file_unique_id, "reusable")

    def test_very_recent_exact_repeat_is_still_suppressed(self) -> None:
        events = [
            text_event(1, "редактор закончил главу рукописи", 1000),
            sticker(2, "too-soon", 1010),
        ]
        candidate = select(
            events,
            rng=random.Random(3),
            actions=(callback("too-soon", NOW - 90),),
        )

        self.assertIsNone(candidate)


if __name__ == "__main__":
    unittest.main()

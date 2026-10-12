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


def sticker(
    message_id: int,
    unique: str,
    created_at: int,
    *,
    caption: str = "редактор закончил главу рукописи",
) -> MemoryEvent:
    return MemoryEvent(
        id=message_id,
        chat_id=CHAT_ID,
        topic_id=TOPIC_ID,
        message_id=message_id,
        user_id=7,
        event_type=MemoryEventType.STICKER,
        created_at=created_at,
        caption=caption,
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


class HighestWeightRng(random.Random):
    """Deterministic probe of the actual selection weights."""

    def choices(self, population, weights=None, *, k=1):
        return [population[max(range(len(population)), key=lambda i: weights[i])]]


def photo(message_id: int, unique: str, created_at: int) -> MemoryEvent:
    return MemoryEvent(
        id=message_id,
        chat_id=CHAT_ID,
        topic_id=TOPIC_ID,
        message_id=message_id,
        user_id=7,
        event_type=MemoryEventType.PHOTO,
        created_at=created_at,
        caption="редактор закончил главу рукописи",
        file_id=f"file-{unique}",
        file_unique_id=unique,
    )


def sent_media(kind: str, *, topic_id: int = TOPIC_ID, offset: int = 800) -> EntertainmentActionRecord:
    return EntertainmentActionRecord(
        id=1, chat_id=CHAT_ID, topic_id=topic_id,
        action_type=EntertainmentActionType.MEMORY_CALLBACK,
        trigger_message_id=None, created_at=NOW - offset,
        metadata={"media_type": kind, "media_file_unique_id": f"used-{offset}"},
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

    def test_inflected_context_still_matches_remembered_media(self) -> None:
        candidate = select_media_candidate(
            [sticker(1, "art", 1000, caption="иллюстрация")],
            (),
            context_messages=("обсуждаем иллюстрации",),
            recent_actions=(),
            now=NOW,
            textual_event_count=100,
            bootstrap_threshold=10_000,
            repeat_cooldown_seconds=21_600,
            rng=random.Random(3),
        )

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.event.file_unique_id, "art")

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

    def test_equally_relevant_photo_is_preferred_after_repeated_stickers(self) -> None:
        events = [
            text_event(1, "редактор закончил главу рукописи", 1000),
            sticker(2, "recent-sticker", 1010),
            photo(3, "relevant-photo", 1020),
        ]
        history = tuple(sent_media("sticker", offset=800 + i * 500) for i in range(4))
        picked = select(events, rng=HighestWeightRng(), actions=history)
        self.assertIsNotNone(picked)
        self.assertEqual(picked.event.event_type, MemoryEventType.PHOTO)

    def test_another_topic_cannot_influence_media_kind_diversity(self) -> None:
        events = [
            text_event(1, "редактор закончил главу рукописи", 1000),
            sticker(2, "recent-sticker", 1010),
            photo(3, "relevant-photo", 1020),
        ]
        foreign_history = tuple(
            sent_media("sticker", topic_id=TOPIC_ID + 1, offset=800 + i * 500)
            for i in range(4)
        )
        picked = select(events, rng=HighestWeightRng(), actions=foreign_history)
        self.assertIsNotNone(picked)
        self.assertEqual(picked.event.event_type, MemoryEventType.STICKER)

    def test_media_variety_never_promotes_an_irrelevant_photo(self) -> None:
        events = [
            text_event(1, "редактор закончил главу рукописи", 1000),
            sticker(2, "relevant-sticker", 1010),
            MemoryEvent(
                id=3, chat_id=CHAT_ID, topic_id=TOPIC_ID, message_id=3,
                user_id=7, event_type=MemoryEventType.PHOTO, created_at=1020,
                caption="зеленая планета и космос", file_id="file-photo",
                file_unique_id="photo",
            ),
        ]
        history = (sent_media("sticker"), sent_media("sticker", offset=1200))
        picked = select(events, rng=HighestWeightRng(), actions=history)
        self.assertIsNotNone(picked)
        self.assertEqual(picked.event.event_type, MemoryEventType.STICKER)

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

from __future__ import annotations

import random
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from entertainment import EntertainmentService
from entertainment.context import ActivitySnapshot
from entertainment.media_culture import MediaCandidate
from entertainment.models import (
    BehaviorMode,
    EntertainmentActionRecord,
    EntertainmentActionType,
    EntertainmentSettings,
    MemoryCounts,
    MemoryEvent,
    MemoryEventType,
)


NOW = 100_000
CHAT_ID = -1001
TOPIC_ID = 10


def text_event(message_id: int, text: str, *, created_at: int = NOW - 100) -> MemoryEvent:
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


def media_event(kind: MemoryEventType, unique: str = "bus-media") -> MemoryEvent:
    return MemoryEvent(
        id=2,
        chat_id=CHAT_ID,
        topic_id=TOPIC_ID,
        message_id=2,
        user_id=7,
        event_type=kind,
        created_at=NOW - 90,
        caption="автобус опоздал на вокзал",
        file_id=f"file-{unique}",
        file_unique_id=unique,
    )


def callback(unique: str, *, created_at: int, direct: bool = False) -> EntertainmentActionRecord:
    return EntertainmentActionRecord(
        id=1,
        chat_id=CHAT_ID,
        topic_id=TOPIC_ID,
        action_type=EntertainmentActionType.MEMORY_CALLBACK,
        trigger_message_id=None,
        created_at=created_at,
        metadata={"media_file_unique_id": unique, "direct": direct},
    )


class FakeMessage:
    def __init__(self, text: str = "автобус опоздал на вокзал") -> None:
        self.chat = SimpleNamespace(id=CHAT_ID, type="supergroup")
        self.from_user = SimpleNamespace(id=7, is_bot=False)
        self.message_id = 700
        self.message_thread_id = TOPIC_ID
        self.text = text
        self.reply_to_message = None
        self.replies: list[str] = []

    async def reply(self, text: str, **_: object) -> None:
        self.replies.append(text)


class FakeBot:
    def __init__(self) -> None:
        self.id = 999
        self.send_sticker = AsyncMock()
        self.send_photo = AsyncMock()
        self.send_animation = AsyncMock()
        self.send_message = AsyncMock()


def activity(*, phase: str = "quiet") -> ActivitySnapshot:
    if phase == "peak":
        return ActivitySnapshot(CHAT_ID, TOPIC_ID, 4, 12, 8, 20, 4, 5.0)
    return ActivitySnapshot(CHAT_ID, TOPIC_ID, 0, 0, 0, 0, 0, 900.0)


class Store:
    def __init__(
        self,
        *,
        settings: EntertainmentSettings | None = None,
        recent_actions: list[EntertainmentActionRecord] | None = None,
        phase: str = "quiet",
    ) -> None:
        self.settings = settings or EntertainmentSettings(behavior_mode=BehaviorMode.ALIVE)
        self.actions = list(recent_actions or [])
        self.phase = phase
        self.recorded: list[EntertainmentActionRecord] = []
        self.recent_action_calls: list[tuple[int, int]] = []

    async def get_settings(self, chat_id: int):
        return self.settings

    async def message_count(self, chat_id: int, topic_id: int | None = None):
        return 25

    async def activity_snapshot(self, chat_id: int, topic_id: int, *, now: int):
        return activity(phase=self.phase)

    async def recent_actions(self, chat_id: int, topic_id: int, *, since: int, limit: int = 20):
        self.recent_action_calls.append((since, limit))
        return [item for item in self.actions if item.created_at >= since][:limit]

    async def human_messages_since(self, chat_id: int, topic_id: int, *, since: int):
        return 20

    async def recent_events(self, chat_id: int, topic_id: int, limit: int):
        return [
            text_event(1, "автобус опоздал на вокзал"),
            media_event(MemoryEventType.STICKER),
        ]

    async def sample_event_windows(self, chat_id: int, topic_id: int, **kwargs):
        return []

    async def memory_counts(self, chat_id: int, topic_id: int):
        return MemoryCounts(total=25, text=24, sticker=1)

    async def record_action(self, record: EntertainmentActionRecord):
        self.recorded.append(record)
        return 900 + len(self.recorded)


class MediaSendHelperTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_helpers_use_telegram_file_id_and_never_copy_caption(self) -> None:
        bot = FakeBot()
        service = EntertainmentService(SimpleNamespace(bot=bot), SimpleNamespace(), {CHAT_ID})

        for kind, method_name, argument_name in (
            (MemoryEventType.STICKER, "send_sticker", "sticker"),
            (MemoryEventType.PHOTO, "send_photo", "photo"),
            (MemoryEventType.ANIMATION, "send_animation", "animation"),
        ):
            with self.subTest(kind=kind):
                event = media_event(kind, unique=f"{kind.value}-u")
                candidate = MediaCandidate(event=event, score=0.9, source_class="human", score_bucket="high")
                await service._send_media_candidate(
                    chat_id=CHAT_ID,
                    topic_id=TOPIC_ID,
                    candidate=candidate,
                )
                method = getattr(bot, method_name)
                kwargs = method.await_args.kwargs
                self.assertEqual(kwargs["chat_id"], CHAT_ID)
                self.assertEqual(kwargs[argument_name], event.file_id)
                self.assertEqual(kwargs["message_thread_id"], TOPIC_ID)
                self.assertNotIn("caption", kwargs)
                method.reset_mock()

    def test_media_repeat_default_is_six_hours(self) -> None:
        from entertainment import config

        self.assertEqual(config.MEDIA_REPEAT_COOLDOWN_SECONDS, 21_600)


class MediaRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def make_service(self, store: Store, bot: FakeBot | None = None) -> tuple[EntertainmentService, FakeBot]:
        bot = bot or FakeBot()
        return (
            EntertainmentService(
                SimpleNamespace(bot=bot),
                store,
                {CHAT_ID},
                rng=random.Random(3),
                now_fn=lambda: float(NOW),
            ),
            bot,
        )

    async def test_contextual_media_realizes_one_already_allowed_action_slot(self) -> None:
        store = Store()
        service, bot = self.make_service(store)
        message = FakeMessage()

        with patch.object(service, "_generate_culture_text", return_value=("fallback text", None)):
            result = await service.evaluate_topic(message)

        self.assertIsNotNone(result)
        self.assertEqual(result.action_type, EntertainmentActionType.MEMORY_CALLBACK)
        bot.send_sticker.assert_awaited_once()
        self.assertEqual(message.replies, [])
        self.assertEqual(len(store.recorded), 1)
        metadata = store.recorded[0].metadata
        self.assertEqual(metadata["media_type"], "sticker")
        self.assertEqual(metadata["media_file_unique_id"], "bus-media")
        self.assertEqual(metadata["media_source_class"], "human")
        self.assertIn(metadata["media_score_bucket"], {"low", "medium", "high"})
        self.assertFalse(metadata["direct"])
        self.assertNotIn("автобус опоздал", repr(metadata))
        self.assertIn((NOW - 21_600, 200), store.recent_action_calls)

    async def test_six_hour_history_is_a_soft_diversity_window_not_a_hard_block(self) -> None:
        old = callback("bus-media", created_at=NOW - 3_600)
        store = Store(recent_actions=[old])
        service, bot = self.make_service(store)
        message = FakeMessage()

        with patch.object(service, "_generate_culture_text", return_value=("fallback text", None)):
            result = await service.evaluate_topic(message)

        self.assertIsNotNone(result)
        self.assertEqual(result.action_type, EntertainmentActionType.MEMORY_CALLBACK)
        bot.send_sticker.assert_awaited_once()
        self.assertEqual(message.replies, [])

    async def test_consecutive_non_direct_media_callback_falls_back_to_text(self) -> None:
        previous = callback("other-media", created_at=NOW - 1_000, direct=False)
        store = Store(recent_actions=[previous])
        service, bot = self.make_service(store)
        message = FakeMessage()

        with patch.object(service, "_generate_culture_text", return_value=("не два медиа подряд", None)):
            result = await service.evaluate_topic(message)

        self.assertIsNotNone(result)
        self.assertEqual(result.action_type, EntertainmentActionType.REMIXED_PHRASE)
        bot.send_sticker.assert_not_awaited()
        self.assertEqual(message.replies, ["не два медиа подряд"])

    async def test_existing_action_budget_can_block_media_entirely(self) -> None:
        previous = EntertainmentActionRecord(
            id=4,
            chat_id=CHAT_ID,
            topic_id=TOPIC_ID,
            action_type=EntertainmentActionType.REMIXED_PHRASE,
            trigger_message_id=None,
            created_at=NOW - 1_000,
            metadata={},
        )
        store = Store(
            settings=EntertainmentSettings(behavior_mode=BehaviorMode.CALM),
            recent_actions=[previous],
        )
        service, bot = self.make_service(store)

        result = await service.evaluate_topic(FakeMessage())

        self.assertIsNone(result)
        bot.send_sticker.assert_not_awaited()
        self.assertEqual(store.recorded, [])

    async def test_non_direct_peak_does_not_send_media(self) -> None:
        store = Store(phase="peak")
        service, bot = self.make_service(store)

        result = await service.evaluate_topic(FakeMessage())

        self.assertIsNone(result)
        bot.send_sticker.assert_not_awaited()
        self.assertEqual(store.recorded, [])

    async def test_media_send_failure_falls_back_once_and_records_only_text_action(self) -> None:
        store = Store()
        bot = FakeBot()
        bot.send_sticker.side_effect = RuntimeError("expired file id")
        service, _ = self.make_service(store, bot)
        message = FakeMessage()

        with patch.object(service, "_generate_culture_text", return_value=("fallback <ok>", None)):
            result = await service.evaluate_topic(message)

        self.assertIsNotNone(result)
        self.assertEqual(result.action_type, EntertainmentActionType.REMIXED_PHRASE)
        self.assertEqual(message.replies, ["fallback &lt;ok&gt;"])
        self.assertEqual(len(store.recorded), 1)
        self.assertEqual(store.recorded[0].action_type, EntertainmentActionType.REMIXED_PHRASE)
        self.assertNotIn("media_file_unique_id", store.recorded[0].metadata)


if __name__ == "__main__":
    unittest.main()

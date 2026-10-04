from __future__ import annotations

import random
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from entertainment import EntertainmentService
from entertainment.models import (
    BehaviorMode,
    EntertainmentActionRecord,
    EntertainmentActionType,
    EntertainmentSettings,
    MemoryCounts,
    MemoryEvent,
    MemoryEventType,
)


CHAT_ID = -1001
TOPIC_ID = 10
NOW = 200_000


class ScriptedRandom(random.Random):
    def __init__(self, *values: float) -> None:
        super().__init__(17)
        self._values = list(values)

    def random(self) -> float:
        if self._values:
            return self._values.pop(0)
        return super().random()


class FakeMessage:
    def __init__(self, text: str) -> None:
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


def sticker_event(message_id: int, unique: str, created_at: int) -> MemoryEvent:
    return MemoryEvent(
        id=message_id,
        chat_id=CHAT_ID,
        topic_id=TOPIC_ID,
        message_id=message_id,
        user_id=7,
        event_type=MemoryEventType.STICKER,
        created_at=created_at,
        file_id=f"file-{unique}",
        file_unique_id=unique,
        sticker_emoji="😴",
    )


def greeting_action(kind: str, created_at: int, output: str = "старый ответ") -> EntertainmentActionRecord:
    return EntertainmentActionRecord(
        id=1,
        chat_id=CHAT_ID,
        topic_id=TOPIC_ID,
        action_type=EntertainmentActionType.CONTEXTUAL_REPLY,
        trigger_message_id=600,
        created_at=created_at,
        metadata={
            "source": "greeting",
            "greeting_kind": kind,
            "greeting_style": "literary",
            "output": output,
        },
    )


class GreetingStore:
    def __init__(
        self,
        *,
        actions: list[EntertainmentActionRecord] | None = None,
        events: list[MemoryEvent] | None = None,
        texts: list[str] | None = None,
    ) -> None:
        self.settings = EntertainmentSettings(behavior_mode=BehaviorMode.ALIVE)
        self.actions = list(actions or [])
        self.events = list(events or [])
        self.texts = list(texts or ["редактор дочитал главу", "рукопись почти готова"])
        self.recorded: list[EntertainmentActionRecord] = []
        self.recent_action_calls: list[tuple[int, int, int, int]] = []

    async def get_settings(self, chat_id: int):
        return self.settings

    async def recent_actions(self, chat_id: int, topic_id: int, *, since: int, limit: int = 20):
        self.recent_action_calls.append((chat_id, topic_id, since, limit))
        return [item for item in self.actions if item.created_at >= since][:limit]

    async def recent_texts(self, chat_id: int, topic_id: int):
        return list(self.texts)

    async def recent_events(self, chat_id: int, topic_id: int, limit: int):
        return list(self.events)

    async def sample_event_windows(self, chat_id: int, topic_id: int, **_: object):
        return []

    async def memory_counts(self, chat_id: int, topic_id: int):
        return MemoryCounts(
            total=len(self.events) + len(self.texts),
            text=len(self.texts) + sum(event.event_type is MemoryEventType.TEXT for event in self.events),
            sticker=sum(event.event_type is MemoryEventType.STICKER for event in self.events),
        )

    async def record_action(self, record: EntertainmentActionRecord):
        self.recorded.append(record)
        return 900 + len(self.recorded)


class GreetingRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def service(self, store: GreetingStore, rng: random.Random) -> tuple[EntertainmentService, FakeBot]:
        bot = FakeBot()
        return (
            EntertainmentService(
                SimpleNamespace(bot=bot),
                store,
                {CHAT_ID},
                rng=rng,
                now_fn=lambda: float(NOW),
            ),
            bot,
        )

    async def test_night_greeting_uses_special_contextual_reply_not_normal_generation(self) -> None:
        store = GreetingStore()
        service, bot = self.service(store, ScriptedRandom(0.0, 0.99))
        message = FakeMessage("спокойной ночи всем")

        with patch.object(service, "_generate_culture_text", return_value=("обычный генератор", None)) as normal:
            result = await service.evaluate_topic(message)

        normal.assert_not_called()
        self.assertIsNotNone(result)
        self.assertEqual(result.action_type, EntertainmentActionType.CONTEXTUAL_REPLY)
        self.assertEqual(len(message.replies), 1)
        self.assertNotEqual(message.replies[0], "обычный генератор")
        bot.send_sticker.assert_not_awaited()
        metadata = store.recorded[0].metadata
        self.assertEqual(metadata["source"], "greeting")
        self.assertEqual(metadata["greeting_kind"], "night")
        self.assertEqual(metadata["greeting_style"], "literary")
        self.assertNotIn("спокойной ночи всем", repr(metadata))

    async def test_greeting_can_deliberately_stay_silent_outside_response_chance(self) -> None:
        store = GreetingStore()
        service, bot = self.service(store, ScriptedRandom(0.99))
        message = FakeMessage("доброе утро")

        with patch.object(service, "_generate_culture_text", return_value=("не должен появиться", None)) as normal:
            result = await service.evaluate_topic(message)

        normal.assert_not_called()
        self.assertIsNone(result)
        self.assertEqual(message.replies, [])
        bot.send_sticker.assert_not_awaited()
        self.assertEqual(store.recorded, [])

    async def test_recent_greeting_in_same_topic_enforces_cooldown(self) -> None:
        store = GreetingStore(actions=[greeting_action("night", NOW - 300)])
        service, _ = self.service(store, ScriptedRandom(0.0))
        message = FakeMessage("споки")

        result = await service.evaluate_topic(message)

        self.assertIsNone(result)
        self.assertEqual(message.replies, [])
        self.assertEqual(store.recorded, [])

    async def test_contextual_remembered_sticker_can_replace_greeting_text(self) -> None:
        events = [
            text_event(1, "спокойной ночи всем", NOW - 600),
            sticker_event(2, "sleep-sticker", NOW - 590),
        ]
        store = GreetingStore(events=events, texts=["редактор закончил главу"])
        service, bot = self.service(store, ScriptedRandom(0.0, 0.0))
        message = FakeMessage("спокойной ночи")

        result = await service.evaluate_topic(message)

        self.assertIsNotNone(result)
        self.assertEqual(result.action_type, EntertainmentActionType.MEMORY_CALLBACK)
        bot.send_sticker.assert_awaited_once()
        self.assertEqual(message.replies, [])
        metadata = store.recorded[0].metadata
        self.assertEqual(metadata["source"], "greeting")
        self.assertEqual(metadata["greeting_kind"], "night")
        self.assertEqual(metadata["media_file_unique_id"], "sleep-sticker")
        self.assertNotIn("спокойной ночи", repr(metadata))


if __name__ == "__main__":
    unittest.main()

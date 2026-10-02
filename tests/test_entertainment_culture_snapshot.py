from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from entertainment import EntertainmentService
from entertainment.culture import CultureMemorySnapshot
from entertainment.models import MemoryCounts, MemoryEvent, MemoryEventType


def event(message_id: int, text: str, *, topic_id: int = 10) -> MemoryEvent:
    return MemoryEvent(
        id=message_id,
        chat_id=-1001,
        topic_id=topic_id,
        message_id=message_id,
        user_id=7,
        event_type=MemoryEventType.TEXT,
        created_at=1000 + message_id,
        text=text,
    )


class CultureSnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_bounded_read_set_builds_generation_and_raw_media_inputs(self) -> None:
        recent = [event(1, "автобус опоздал"), event(2, "/spawn bus")]
        history = [[event(10, "старый автобус")]]
        counts = MemoryCounts(total=10_000, text=10_000)
        store = SimpleNamespace(
            recent_events=AsyncMock(return_value=recent),
            sample_event_windows=AsyncMock(return_value=history),
            memory_counts=AsyncMock(return_value=counts),
            recent_texts=AsyncMock(return_value=[]),
            recent_messages=AsyncMock(return_value=[]),
        )
        service = EntertainmentService(
            SimpleNamespace(bot=SimpleNamespace(id=999)),
            store,
            {-1001},
            now_fn=lambda: 5000.0,
        )

        snapshot = await service._culture_memory_snapshot(
            -1001,
            10,
            trigger_text="где автобус",
            now=5000,
        )

        self.assertIsInstance(snapshot, CultureMemorySnapshot)
        self.assertEqual(snapshot.recent_events, recent)
        self.assertEqual(snapshot.historical_windows, history)
        self.assertEqual(snapshot.counts, counts)
        self.assertIn("где автобус", snapshot.generation.context_messages)
        store.recent_events.assert_awaited_once()
        store.sample_event_windows.assert_awaited_once()
        store.memory_counts.assert_awaited_once_with(-1001, 10)
        store.recent_texts.assert_not_awaited()
        store.recent_messages.assert_not_awaited()

    async def test_snapshot_defensively_filters_wrong_topic_events(self) -> None:
        store = SimpleNamespace(
            recent_events=AsyncMock(return_value=[event(1, "нужная тема"), event(2, "чужая тема", topic_id=99)]),
            sample_event_windows=AsyncMock(return_value=[[event(3, "история"), event(4, "чужая история", topic_id=99)]]),
            memory_counts=AsyncMock(return_value=MemoryCounts(total=2, text=2)),
            recent_texts=AsyncMock(return_value=[]),
            recent_messages=AsyncMock(return_value=[]),
        )
        service = EntertainmentService(SimpleNamespace(bot=SimpleNamespace(id=999)), store, {-1001})

        snapshot = await service._culture_memory_snapshot(-1001, 10, now=5000)

        self.assertEqual([item.topic_id for item in snapshot.recent_events], [10])
        self.assertEqual([[item.topic_id for item in window] for window in snapshot.historical_windows], [[10]])
        self.assertNotIn("чужая тема", snapshot.generation.source_messages)
        self.assertNotIn("чужая история", snapshot.generation.source_messages)

    async def test_legacy_storage_fallback_is_text_only_and_does_not_scan_history(self) -> None:
        store = SimpleNamespace(
            recent_texts=AsyncMock(return_value=["один два", "три четыре"]),
            recent_messages=AsyncMock(return_value=[]),
        )
        service = EntertainmentService(
            SimpleNamespace(bot=SimpleNamespace(id=999)),
            store,
            {-1001},
            now_fn=lambda: 5000.0,
        )

        snapshot = await service._culture_memory_snapshot(
            -1001,
            10,
            trigger_text="живой триггер",
            now=5000,
        )

        self.assertEqual(snapshot.recent_events, [])
        self.assertEqual(snapshot.historical_windows, [])
        self.assertEqual(snapshot.counts.text, 2)
        self.assertEqual(snapshot.generation.source_messages, ["один два", "три четыре"])
        self.assertEqual(snapshot.generation.context_messages[-2:], ["живой триггер", "живой триггер"])
        store.recent_texts.assert_awaited_once_with(-1001, 10)
        store.recent_messages.assert_not_awaited()

    async def test_compat_generation_context_uses_snapshot_generation(self) -> None:
        recent = [event(1, "автобус сейчас")]
        store = SimpleNamespace(
            recent_events=AsyncMock(return_value=recent),
            sample_event_windows=AsyncMock(return_value=[]),
            memory_counts=AsyncMock(return_value=MemoryCounts(total=1, text=1)),
        )
        service = EntertainmentService(SimpleNamespace(bot=SimpleNamespace(id=999)), store, {-1001})

        context = await service._culture_generation_context(-1001, 10, now=5000)

        self.assertIn("автобус сейчас", context.source_messages)
        store.recent_events.assert_awaited_once()
        store.sample_event_windows.assert_awaited_once()
        store.memory_counts.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()

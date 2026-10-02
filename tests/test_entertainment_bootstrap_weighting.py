from __future__ import annotations

import unittest
from collections import Counter
from types import SimpleNamespace
from unittest.mock import AsyncMock

from entertainment import EntertainmentService
from entertainment.culture import build_culture_context
from entertainment.models import MemoryCounts, MemoryEvent, MemoryEventType


def event(
    message_id: int,
    text: str,
    *,
    user_id: int = 1,
    created_at: int = 1000,
    sender_is_bot: bool = False,
    is_command: bool = False,
) -> MemoryEvent:
    return MemoryEvent(
        id=message_id,
        chat_id=-1001,
        topic_id=10,
        message_id=message_id,
        user_id=user_id,
        event_type=MemoryEventType.TEXT,
        created_at=created_at,
        text=text,
        sender_is_bot=sender_is_bot,
        is_command=is_command,
    )


class BootstrapWeightingTests(unittest.TestCase):
    def test_pre_threshold_commands_and_other_bots_keep_normal_recent_weight(self) -> None:
        recent = [
            event(1, "обычная человеческая речь", created_at=1000),
            event(2, "/spawn mythic", created_at=2000, is_command=True),
            event(3, "рейд появился", created_at=3000, sender_is_bot=True),
        ]
        context = build_culture_context(
            recent,
            [],
            textual_event_count=9_999,
            bootstrap_threshold=10_000,
        )
        counts = Counter(context.source_messages)
        self.assertEqual(counts["/spawn mythic"], counts["обычная человеческая речь"])
        self.assertEqual(counts["рейд появился"], counts["обычная человеческая речь"])

    def test_at_threshold_commands_and_other_bots_are_reduced_to_forty_percent(self) -> None:
        recent = [
            event(1, "обычная человеческая речь", created_at=1000),
            event(2, "/spawn mythic", created_at=2000, is_command=True),
            event(3, "рейд появился", created_at=3000, sender_is_bot=True),
        ]
        context = build_culture_context(
            recent,
            [],
            textual_event_count=10_000,
            bootstrap_threshold=10_000,
        )
        counts = Counter(context.source_messages)
        self.assertEqual(counts["обычная человеческая речь"], 10)
        self.assertEqual(counts["/spawn mythic"], 4)
        self.assertEqual(counts["рейд появился"], 4)
        self.assertIn("/spawn mythic", context.source_messages)
        self.assertIn("рейд появился", context.source_messages)

    def test_reduced_recent_source_still_outweighs_reduced_historical_source(self) -> None:
        recent = [event(10, "/spawn recent", created_at=5000, is_command=True)]
        historical = [[event(1, "/spawn old", created_at=1000, is_command=True)]]
        context = build_culture_context(
            recent,
            historical,
            textual_event_count=10_000,
            bootstrap_threshold=10_000,
        )
        counts = Counter(context.source_messages)
        self.assertGreater(counts["/spawn recent"], counts["/spawn old"])
        self.assertGreater(counts["/spawn old"], 0)


class BootstrapWeightingServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_phase_c_service_reads_topic_counts_once_and_passes_threshold_state(self) -> None:
        recent = [event(1, "/spawn service", created_at=1000, is_command=True)]
        store = SimpleNamespace(
            recent_events=AsyncMock(return_value=recent),
            sample_event_windows=AsyncMock(return_value=[]),
            memory_counts=AsyncMock(return_value=MemoryCounts(total=10_000, text=10_000)),
            recent_texts=AsyncMock(return_value=[]),
            recent_messages=AsyncMock(return_value=[]),
        )
        service = EntertainmentService(
            SimpleNamespace(bot=SimpleNamespace(id=999)),
            store,
            {-1001},
            now_fn=lambda: 5000.0,
        )

        context = await service._culture_generation_context(-1001, 10, now=5000)

        store.memory_counts.assert_awaited_once_with(-1001, 10)
        self.assertEqual(Counter(context.source_messages)["/spawn service"], 4)


if __name__ == "__main__":
    unittest.main()

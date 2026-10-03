from __future__ import annotations

import unittest
from collections import Counter
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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
    def test_pre_threshold_seed_does_not_reduce_commands_or_other_bots(self) -> None:
        recent = [
            event(1, "обычная человеческая речь"),
            event(2, "/spawn mythic", is_command=True),
            event(3, "рейд появился", sender_is_bot=True),
        ]
        first = build_culture_context(
            recent,
            [],
            textual_event_count=9_999,
            bootstrap_threshold=10_000,
            weight_seed=1,
        )
        second = build_culture_context(
            recent,
            [],
            textual_event_count=9_999,
            bootstrap_threshold=10_000,
            weight_seed=999,
        )

        first_counts = Counter(first.source_messages)
        second_counts = Counter(second.source_messages)
        self.assertEqual(first_counts, second_counts)
        self.assertEqual(first_counts["/spawn mythic"], first_counts["обычная человеческая речь"])
        self.assertEqual(first_counts["рейд появился"], first_counts["обычная человеческая речь"])

    def test_at_threshold_seed_keeps_two_of_five_special_sources(self) -> None:
        recent = [
            event(index, f"special {index}", is_command=True)
            for index in range(1, 6)
        ]
        context = build_culture_context(
            recent,
            [],
            textual_event_count=10_000,
            bootstrap_threshold=10_000,
            weight_seed=6,
        )
        counts = Counter(context.source_messages)
        present = [index for index in range(1, 6) if counts[f"special {index}"] > 0]

        self.assertEqual(present, [3, 4])
        self.assertEqual(counts["special 3"], 10)
        self.assertEqual(counts["special 4"], 10)

    def test_changing_seed_can_restore_previously_omitted_source(self) -> None:
        source = [event(1, "/spawn return", is_command=True)]
        omitted = build_culture_context(
            source,
            [],
            textual_event_count=10_000,
            bootstrap_threshold=10_000,
            weight_seed=6,
        )
        restored = build_culture_context(
            source,
            [],
            textual_event_count=10_000,
            bootstrap_threshold=10_000,
            weight_seed=1,
        )

        self.assertNotIn("/spawn return", omitted.source_messages)
        self.assertIn("/spawn return", restored.source_messages)

    def test_human_source_is_never_sampled_out_after_bootstrap(self) -> None:
        source = [event(1, "обычная человеческая речь")]
        for seed in (1, 6, 999):
            context = build_culture_context(
                source,
                [],
                textual_event_count=10_000,
                bootstrap_threshold=10_000,
                weight_seed=seed,
            )
            self.assertEqual(Counter(context.source_messages)["обычная человеческая речь"], 10)


class BootstrapWeightingServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_phase_c_service_reads_topic_counts_once_and_passes_seed_and_threshold(self) -> None:
        recent = [event(1, "/spawn service", is_command=True)]
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

        with patch("entertainment.culture_service.build_culture_context") as builder:
            builder.return_value = SimpleNamespace()
            await service._culture_memory_snapshot(-1001, 10, now=5000)

        store.memory_counts.assert_awaited_once_with(-1001, 10)
        kwargs = builder.call_args.kwargs
        self.assertEqual(kwargs["textual_event_count"], 10_000)
        self.assertEqual(kwargs["bootstrap_threshold"], 10_000)
        self.assertEqual(kwargs["weight_seed"], service._culture_seed(-1001, 10, 5000))


if __name__ == "__main__":
    unittest.main()

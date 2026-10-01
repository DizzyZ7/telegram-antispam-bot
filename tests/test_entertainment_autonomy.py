from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from entertainment.autonomy import ConversationPhase, derive_phase
from entertainment.context import ActivitySnapshot
from entertainment.storage.sqlite import SQLiteEntertainmentStorage


class ConversationPhaseTests(unittest.TestCase):
    def snapshot(
        self,
        *,
        messages_1m: int = 0,
        messages_5m: int = 0,
        messages_previous_5m: int = 0,
        messages_15m: int = 0,
        active_users_5m: int = 0,
        seconds_since_human: float | None = None,
    ) -> ActivitySnapshot:
        return ActivitySnapshot(
            chat_id=-1001,
            topic_id=10,
            messages_1m=messages_1m,
            messages_5m=messages_5m,
            messages_previous_5m=messages_previous_5m,
            messages_15m=messages_15m,
            active_users_5m=active_users_5m,
            seconds_since_human=seconds_since_human,
        )

    def test_quiet_when_no_recent_messages_and_at_most_one_in_fifteen_minutes(self) -> None:
        self.assertEqual(
            derive_phase(self.snapshot(messages_5m=0, messages_15m=0)),
            ConversationPhase.QUIET,
        )
        self.assertEqual(
            derive_phase(self.snapshot(messages_5m=0, messages_15m=1)),
            ConversationPhase.QUIET,
        )

    def test_warming_up_boundaries(self) -> None:
        self.assertEqual(
            derive_phase(self.snapshot(messages_5m=0, messages_15m=2)),
            ConversationPhase.WARMING_UP,
        )
        self.assertEqual(
            derive_phase(self.snapshot(messages_5m=1, messages_15m=2, active_users_5m=1)),
            ConversationPhase.WARMING_UP,
        )
        self.assertEqual(
            derive_phase(self.snapshot(messages_5m=4, messages_15m=4, active_users_5m=2)),
            ConversationPhase.WARMING_UP,
        )

    def test_active_starts_at_five_and_holds_below_peak(self) -> None:
        self.assertEqual(
            derive_phase(self.snapshot(messages_5m=5, messages_15m=5, active_users_5m=2)),
            ConversationPhase.ACTIVE,
        )
        self.assertEqual(
            derive_phase(self.snapshot(messages_5m=11, messages_15m=11, active_users_5m=5)),
            ConversationPhase.ACTIVE,
        )

    def test_peak_requires_twelve_messages_and_three_active_users(self) -> None:
        self.assertEqual(
            derive_phase(self.snapshot(messages_5m=12, messages_15m=12, active_users_5m=3)),
            ConversationPhase.PEAK,
        )
        self.assertEqual(
            derive_phase(self.snapshot(messages_5m=12, messages_15m=12, active_users_5m=2)),
            ConversationPhase.ACTIVE,
        )

    def test_cooldown_precedes_warming_up_after_a_burst(self) -> None:
        self.assertEqual(
            derive_phase(
                self.snapshot(
                    messages_5m=1,
                    messages_previous_5m=8,
                    messages_15m=9,
                    active_users_5m=1,
                )
            ),
            ConversationPhase.COOLDOWN,
        )
        self.assertEqual(
            derive_phase(
                self.snapshot(
                    messages_5m=4,
                    messages_previous_5m=8,
                    messages_15m=12,
                    active_users_5m=2,
                )
            ),
            ConversationPhase.COOLDOWN,
        )


class SQLiteActivitySnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "activity.db"
        self.storage = SQLiteEntertainmentStorage(self.database_path)
        await self.storage.initialize()

    async def asyncTearDown(self) -> None:
        await self.storage.close()
        self.temp_dir.cleanup()

    async def test_activity_snapshot_uses_exact_rolling_windows_and_topic_isolation(self) -> None:
        now = 10_000
        # topic 10: 1m, 5m, previous 5m and 15m buckets
        await self.storage.add_message(-1001, 10, 1, "fresh one", created_at=9_980)
        await self.storage.add_message(-1001, 10, 2, "fresh two", created_at=9_940)
        await self.storage.add_message(-1001, 10, 1, "five minute edge", created_at=9_700)
        await self.storage.add_message(-1001, 10, 3, "previous five", created_at=9_699)
        await self.storage.add_message(-1001, 10, 4, "previous older", created_at=9_450)
        await self.storage.add_message(-1001, 10, 5, "fifteen minute edge", created_at=9_100)
        await self.storage.add_message(-1001, 10, 6, "too old", created_at=9_099)

        # Same chat, sibling topic: must not leak into topic 10.
        await self.storage.add_message(-1001, 20, 99, "other topic", created_at=9_990)
        # Other chat: must not leak either.
        await self.storage.add_message(-1002, 10, 98, "other chat", created_at=9_995)

        snapshot = await self.storage.activity_snapshot(-1001, 10, now=now)
        self.assertEqual(snapshot.messages_1m, 2)
        self.assertEqual(snapshot.messages_5m, 3)
        self.assertEqual(snapshot.messages_previous_5m, 2)
        self.assertEqual(snapshot.messages_15m, 6)
        self.assertEqual(snapshot.active_users_5m, 2)
        self.assertEqual(snapshot.seconds_since_human, 20.0)

        sibling = await self.storage.activity_snapshot(-1001, 20, now=now)
        self.assertEqual(sibling.messages_5m, 1)
        self.assertEqual(sibling.active_users_5m, 1)
        self.assertEqual(sibling.seconds_since_human, 10.0)

    async def test_empty_topic_has_none_last_human_age(self) -> None:
        snapshot = await self.storage.activity_snapshot(-1001, 777, now=10_000)
        self.assertEqual(snapshot.messages_15m, 0)
        self.assertIsNone(snapshot.seconds_since_human)


if __name__ == "__main__":
    unittest.main()

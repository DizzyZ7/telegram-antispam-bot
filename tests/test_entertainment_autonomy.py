from __future__ import annotations

import random
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from entertainment.autonomy import (
    ActionCandidate,
    ConversationPhase,
    DecisionContext,
    behavior_policy,
    derive_phase,
    select_action,
)
from entertainment.context import ActivitySnapshot
from entertainment.models import (
    BehaviorMode,
    EntertainmentActionRecord,
    EntertainmentActionType,
    EntertainmentSettings,
)
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


class AutonomousDecisionTests(unittest.TestCase):
    NOW = 100_000

    def activity(self, *, messages_5m: int = 3, messages_15m: int = 6) -> ActivitySnapshot:
        return ActivitySnapshot(
            chat_id=-1001,
            topic_id=10,
            messages_1m=min(messages_5m, 1),
            messages_5m=messages_5m,
            messages_previous_5m=max(0, messages_15m - messages_5m),
            messages_15m=messages_15m,
            active_users_5m=2,
            seconds_since_human=30.0,
        )

    def candidate(
        self,
        action_type: EntertainmentActionType = EntertainmentActionType.REMIXED_PHRASE,
        *,
        relevance: float = 0.8,
        novelty: float = 0.8,
        annoyance_cost: float = 0.1,
        trigger_message_id: int | None = None,
    ) -> ActionCandidate:
        return ActionCandidate(
            action_type=action_type,
            relevance=relevance,
            novelty=novelty,
            annoyance_cost=annoyance_cost,
            trigger_message_id=trigger_message_id,
        )

    def action(
        self,
        *,
        seconds_ago: int,
        action_type: EntertainmentActionType = EntertainmentActionType.REMIXED_PHRASE,
    ) -> EntertainmentActionRecord:
        return EntertainmentActionRecord(
            id=1,
            chat_id=-1001,
            topic_id=10,
            action_type=action_type,
            trigger_message_id=None,
            created_at=self.NOW - seconds_ago,
            metadata={},
        )

    def context(
        self,
        *,
        settings: EntertainmentSettings | None = None,
        phase: ConversationPhase = ConversationPhase.COOLDOWN,
        recent_actions: tuple[EntertainmentActionRecord, ...] = (),
        human_messages: int = 10,
        quiet_hours_active: bool = False,
        memory_count: int = 100,
    ) -> DecisionContext:
        return DecisionContext(
            settings=settings or EntertainmentSettings(),
            phase=phase,
            activity=self.activity(),
            recent_actions=recent_actions,
            human_messages_since_last_action=human_messages,
            memory_count=memory_count,
            quiet_hours_active=quiet_hours_active,
            now=self.NOW,
        )

    def previous_other_type(self, *, seconds_ago: int) -> EntertainmentActionRecord:
        return self.action(
            seconds_ago=seconds_ago,
            action_type=EntertainmentActionType.CONTEXTUAL_REPLY,
        )

    def test_policies_have_exact_limits(self) -> None:
        calm = behavior_policy(BehaviorMode.CALM)
        alive = behavior_policy(BehaviorMode.ALIVE)
        active = behavior_policy(BehaviorMode.ACTIVE)
        self.assertEqual((calm.max_actions_30m, calm.min_gap_seconds, calm.min_human_messages_between), (1, 900, 8))
        self.assertEqual((alive.max_actions_30m, alive.min_gap_seconds, alive.min_human_messages_between), (2, 360, 4))
        self.assertEqual((active.max_actions_30m, active.min_gap_seconds, active.min_human_messages_between), (3, 180, 2))

    def test_disabled_autonomous_text_and_quiet_hours_block(self) -> None:
        base = self.context()
        for settings, quiet in (
            (replace(base.settings, enabled=False), False),
            (replace(base.settings, autonomous_text_enabled=False), False),
            (base.settings, True),
        ):
            context = replace(base, settings=settings, quiet_hours_active=quiet)
            self.assertIsNone(select_action(context, [self.candidate()], rng=random.Random(1)))

    def test_alive_rolling_budget_blocks_second_completed_budget(self) -> None:
        actions = (
            self.action(seconds_ago=1_700),
            self.action(seconds_ago=700, action_type=EntertainmentActionType.CONTEXTUAL_REPLY),
        )
        self.assertIsNone(
            select_action(
                self.context(recent_actions=actions, human_messages=10),
                [self.candidate()],
                rng=random.Random(1),
            )
        )

    def test_old_actions_outside_thirty_minutes_do_not_consume_budget(self) -> None:
        actions = (self.previous_other_type(seconds_ago=1_801),)
        selected = select_action(
            self.context(recent_actions=actions, human_messages=10),
            [self.candidate()],
            rng=random.Random(1),
        )
        self.assertIsNotNone(selected)

    def test_min_gap_boundary_is_inclusive(self) -> None:
        blocked = self.context(recent_actions=(self.previous_other_type(seconds_ago=359),), human_messages=10)
        allowed = self.context(recent_actions=(self.previous_other_type(seconds_ago=360),), human_messages=10)
        self.assertIsNone(select_action(blocked, [self.candidate()], rng=random.Random(1)))
        self.assertIsNotNone(select_action(allowed, [self.candidate()], rng=random.Random(1)))

    def test_human_message_budget_blocks_zero_and_below_policy_minimum(self) -> None:
        actions = (self.previous_other_type(seconds_ago=500),)
        self.assertIsNone(
            select_action(
                self.context(recent_actions=actions, human_messages=0),
                [self.candidate()],
                rng=random.Random(1),
            )
        )
        self.assertIsNone(
            select_action(
                self.context(recent_actions=actions, human_messages=3),
                [self.candidate()],
                rng=random.Random(1),
            )
        )
        self.assertIsNotNone(
            select_action(
                self.context(recent_actions=actions, human_messages=4),
                [self.candidate()],
                rng=random.Random(1),
            )
        )

    def test_peak_rejects_ordinary_autonomous_text_but_allows_direct_contextual_reply(self) -> None:
        peak = self.context(phase=ConversationPhase.PEAK)
        self.assertIsNone(select_action(peak, [self.candidate()], rng=random.Random(1)))
        direct = self.candidate(
            EntertainmentActionType.CONTEXTUAL_REPLY,
            trigger_message_id=777,
            relevance=0.95,
            novelty=0.85,
            annoyance_cost=0.05,
        )
        self.assertEqual(select_action(peak, [direct], rng=random.Random(1)), direct)

    def test_non_direct_same_action_type_is_not_repeated_back_to_back(self) -> None:
        context = self.context(
            recent_actions=(self.action(seconds_ago=500, action_type=EntertainmentActionType.REMIXED_PHRASE),),
            human_messages=10,
        )
        self.assertIsNone(select_action(context, [self.candidate()], rng=random.Random(1)))
        direct = self.candidate(
            EntertainmentActionType.CONTEXTUAL_REPLY,
            trigger_message_id=888,
        )
        context = replace(
            context,
            recent_actions=(self.action(seconds_ago=500, action_type=EntertainmentActionType.CONTEXTUAL_REPLY),),
        )
        self.assertEqual(select_action(context, [direct], rng=random.Random(1)), direct)

    def test_score_threshold_rejects_weak_candidate(self) -> None:
        weak = self.candidate(relevance=0.3, novelty=0.3, annoyance_cost=0.5)
        self.assertIsNone(select_action(self.context(), [weak], rng=random.Random(1)))

    def test_best_scoring_eligible_candidate_is_selected(self) -> None:
        low = self.candidate(
            EntertainmentActionType.MEMORY_CALLBACK,
            relevance=0.62,
            novelty=0.62,
            annoyance_cost=0.2,
        )
        high = self.candidate(
            EntertainmentActionType.CONTEXTUAL_REPLY,
            relevance=0.95,
            novelty=0.85,
            annoyance_cost=0.05,
            trigger_message_id=999,
        )
        selected = select_action(self.context(), [low, high], rng=random.Random(3))
        self.assertEqual(selected, high)


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
        await self.storage.add_message(-1001, 10, 1, "fresh one", created_at=9_980)
        await self.storage.add_message(-1001, 10, 2, "fresh two", created_at=9_940)
        await self.storage.add_message(-1001, 10, 1, "five minute edge", created_at=9_700)
        await self.storage.add_message(-1001, 10, 3, "previous five", created_at=9_699)
        await self.storage.add_message(-1001, 10, 4, "previous older", created_at=9_450)
        await self.storage.add_message(-1001, 10, 5, "fifteen minute edge", created_at=9_100)
        await self.storage.add_message(-1001, 10, 6, "too old", created_at=9_099)
        await self.storage.add_message(-1001, 20, 99, "other topic", created_at=9_990)
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

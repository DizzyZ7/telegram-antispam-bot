from __future__ import annotations

import random
import unittest

import entertainment.autonomy as autonomy
from entertainment.context import ActivitySnapshot
from entertainment.models import (
    BehaviorMode,
    EntertainmentActionRecord,
    EntertainmentActionType,
    EntertainmentSettings,
)


class AdaptivePresencePolicyTests(unittest.TestCase):
    NOW = 100_000

    def activity(
        self,
        *,
        messages_5m: int = 0,
        messages_15m: int = 0,
        messages_60m: int = 0,
        messages_120m: int = 0,
        active_users_5m: int = 0,
        active_users_60m: int = 0,
        seconds_since_human: float | None = 120.0,
        seconds_since_bot_action: float | None = None,
        human_messages_since_bot_action: int | None = None,
    ) -> ActivitySnapshot:
        return ActivitySnapshot(
            chat_id=-1001,
            topic_id=10,
            messages_1m=min(messages_5m, 1),
            messages_5m=messages_5m,
            messages_previous_5m=max(0, messages_15m - messages_5m),
            messages_15m=messages_15m,
            active_users_5m=active_users_5m,
            seconds_since_human=seconds_since_human,
            messages_60m=messages_60m,
            messages_120m=messages_120m,
            active_users_60m=active_users_60m,
            seconds_since_bot_action=seconds_since_bot_action,
            human_messages_since_bot_action=human_messages_since_bot_action,
        )

    def action(self, *, seconds_ago: int) -> EntertainmentActionRecord:
        return EntertainmentActionRecord(
            id=1,
            chat_id=-1001,
            topic_id=10,
            action_type=EntertainmentActionType.CONTEXTUAL_REPLY,
            trigger_message_id=None,
            created_at=self.NOW - seconds_ago,
            metadata={"source": "message"},
        )

    def context(
        self,
        *,
        phase: autonomy.ConversationPhase,
        activity: ActivitySnapshot,
        last_action_seconds_ago: int | None,
        human_messages: int,
        mode: BehaviorMode = BehaviorMode.ALIVE,
    ) -> autonomy.DecisionContext:
        return autonomy.DecisionContext(
            settings=EntertainmentSettings(behavior_mode=mode),
            phase=phase,
            activity=activity,
            recent_actions=(
                (self.action(seconds_ago=last_action_seconds_ago),)
                if last_action_seconds_ago is not None
                else ()
            ),
            human_messages_since_last_action=human_messages,
            memory_count=10_000,
            quiet_hours_active=False,
            now=self.NOW,
        )

    @staticmethod
    def candidate() -> autonomy.ActionCandidate:
        return autonomy.ActionCandidate(
            action_type=EntertainmentActionType.REMIXED_PHRASE,
            relevance=0.9,
            novelty=0.9,
            annoyance_cost=0.05,
        )

    def test_activity_snapshot_exposes_long_presence_horizon(self) -> None:
        fields = ActivitySnapshot.__dataclass_fields__
        self.assertIn("messages_60m", fields)
        self.assertIn("messages_120m", fields)
        self.assertIn("active_users_60m", fields)
        self.assertIn("seconds_since_bot_action", fields)
        self.assertIn("human_messages_since_bot_action", fields)

    def test_alive_policy_gets_quieter_when_topic_is_quiet(self) -> None:
        factory = getattr(autonomy, "adaptive_presence_policy", None)
        self.assertTrue(callable(factory), "adaptive_presence_policy must exist")
        quiet = factory(
            BehaviorMode.ALIVE,
            autonomy.ConversationPhase.QUIET,
            self.activity(messages_60m=4, messages_120m=8, active_users_60m=2),
        )
        active = factory(
            BehaviorMode.ALIVE,
            autonomy.ConversationPhase.ACTIVE,
            self.activity(
                messages_5m=7,
                messages_15m=18,
                messages_60m=45,
                messages_120m=80,
                active_users_5m=4,
                active_users_60m=10,
            ),
        )
        self.assertGreaterEqual(quiet.min_gap_seconds, 3_600)
        self.assertGreater(quiet.min_gap_seconds, active.min_gap_seconds)
        self.assertGreaterEqual(active.min_human_messages_between, 8)
        self.assertLessEqual(active.min_gap_seconds, 1_200)

    def test_recently_busy_topic_cools_down_faster_than_truly_quiet_topic(self) -> None:
        factory = getattr(autonomy, "adaptive_presence_policy", None)
        self.assertTrue(callable(factory), "adaptive_presence_policy must exist")
        quiet = factory(
            BehaviorMode.ALIVE,
            autonomy.ConversationPhase.QUIET,
            self.activity(messages_60m=3, messages_120m=5, active_users_60m=2),
        )
        recently_busy = factory(
            BehaviorMode.ALIVE,
            autonomy.ConversationPhase.QUIET,
            self.activity(messages_60m=18, messages_120m=30, active_users_60m=6),
        )
        self.assertGreater(quiet.min_gap_seconds, recently_busy.min_gap_seconds)
        self.assertGreaterEqual(recently_busy.min_gap_seconds, 1_800)

    def test_alive_quiet_topic_needs_about_an_hour_and_human_budget(self) -> None:
        activity = self.activity(
            messages_60m=4,
            messages_120m=8,
            active_users_60m=2,
            seconds_since_human=1_800,
        )
        blocked_time = self.context(
            phase=autonomy.ConversationPhase.QUIET,
            activity=activity,
            last_action_seconds_ago=3_599,
            human_messages=4,
        )
        blocked_humans = self.context(
            phase=autonomy.ConversationPhase.QUIET,
            activity=activity,
            last_action_seconds_ago=3_600,
            human_messages=1,
        )
        allowed = self.context(
            phase=autonomy.ConversationPhase.QUIET,
            activity=activity,
            last_action_seconds_ago=3_600,
            human_messages=2,
        )
        rng = random.Random(3)
        self.assertIsNone(autonomy.select_action(blocked_time, [self.candidate()], rng=rng))
        self.assertIsNone(autonomy.select_action(blocked_humans, [self.candidate()], rng=random.Random(3)))
        self.assertIsNotNone(autonomy.select_action(allowed, [self.candidate()], rng=random.Random(3)))

    def test_bot_action_older_than_thirty_minutes_still_consumes_quiet_presence_gap(self) -> None:
        blocked = self.context(
            phase=autonomy.ConversationPhase.QUIET,
            activity=self.activity(
                messages_60m=5,
                messages_120m=8,
                active_users_60m=3,
                seconds_since_bot_action=2_400,
                human_messages_since_bot_action=4,
            ),
            last_action_seconds_ago=None,
            human_messages=0,
        )
        allowed = self.context(
            phase=autonomy.ConversationPhase.QUIET,
            activity=self.activity(
                messages_60m=5,
                messages_120m=8,
                active_users_60m=3,
                seconds_since_bot_action=3_600,
                human_messages_since_bot_action=2,
            ),
            last_action_seconds_ago=None,
            human_messages=0,
        )
        self.assertIsNone(autonomy.select_action(blocked, [self.candidate()], rng=random.Random(6)))
        self.assertIsNotNone(autonomy.select_action(allowed, [self.candidate()], rng=random.Random(6)))

    def test_active_topic_requires_many_human_messages_even_when_time_gap_passed(self) -> None:
        activity = self.activity(
            messages_5m=7,
            messages_15m=18,
            messages_60m=50,
            messages_120m=90,
            active_users_5m=4,
            active_users_60m=12,
        )
        blocked = self.context(
            phase=autonomy.ConversationPhase.ACTIVE,
            activity=activity,
            last_action_seconds_ago=1_200,
            human_messages=7,
        )
        allowed = self.context(
            phase=autonomy.ConversationPhase.ACTIVE,
            activity=activity,
            last_action_seconds_ago=1_200,
            human_messages=10,
        )
        self.assertIsNone(autonomy.select_action(blocked, [self.candidate()], rng=random.Random(4)))
        self.assertIsNotNone(autonomy.select_action(allowed, [self.candidate()], rng=random.Random(4)))

    def test_truly_dead_topic_does_not_self_start_from_one_old_message(self) -> None:
        context = self.context(
            phase=autonomy.ConversationPhase.QUIET,
            activity=self.activity(
                messages_60m=0,
                messages_120m=1,
                active_users_60m=0,
                seconds_since_human=5_000,
            ),
            last_action_seconds_ago=7_000,
            human_messages=2,
        )
        self.assertIsNone(
            autonomy.select_action(context, [self.candidate()], rng=random.Random(5))
        )


if __name__ == "__main__":
    unittest.main()

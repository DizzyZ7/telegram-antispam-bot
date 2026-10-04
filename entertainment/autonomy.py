"""Pure, deterministic autonomy decisions derived from topic activity."""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum

from .context import ActivitySnapshot
from .models import (
    BehaviorMode,
    EntertainmentActionRecord,
    EntertainmentActionType,
    EntertainmentSettings,
)


class ConversationPhase(str, Enum):
    QUIET = "quiet"
    WARMING_UP = "warming_up"
    ACTIVE = "active"
    PEAK = "peak"
    COOLDOWN = "cooldown"


@dataclass(frozen=True, slots=True)
class BehaviorPolicy:
    max_actions_30m: int
    min_gap_seconds: int
    min_human_messages_between: int


@dataclass(frozen=True, slots=True)
class ActionCandidate:
    action_type: EntertainmentActionType
    relevance: float
    novelty: float
    annoyance_cost: float
    trigger_message_id: int | None = None

    @property
    def is_direct(self) -> bool:
        return (
            self.action_type is EntertainmentActionType.CONTEXTUAL_REPLY
            and self.trigger_message_id is not None
        )


@dataclass(frozen=True, slots=True)
class DecisionContext:
    settings: EntertainmentSettings
    phase: ConversationPhase
    activity: ActivitySnapshot
    recent_actions: tuple[EntertainmentActionRecord, ...]
    human_messages_since_last_action: int
    memory_count: int
    quiet_hours_active: bool
    now: int


_POLICIES = {
    BehaviorMode.CALM: BehaviorPolicy(1, 900, 8),
    BehaviorMode.ALIVE: BehaviorPolicy(2, 360, 4),
    BehaviorMode.ACTIVE: BehaviorPolicy(3, 180, 2),
}

_ADAPTIVE_POLICIES: dict[BehaviorMode, dict[ConversationPhase, BehaviorPolicy]] = {
    BehaviorMode.CALM: {
        ConversationPhase.QUIET: BehaviorPolicy(1, 5_400, 3),
        ConversationPhase.COOLDOWN: BehaviorPolicy(1, 3_600, 6),
        ConversationPhase.WARMING_UP: BehaviorPolicy(1, 2_700, 8),
        ConversationPhase.ACTIVE: BehaviorPolicy(1, 1_800, 14),
        ConversationPhase.PEAK: BehaviorPolicy(1, 3_600, 20),
    },
    BehaviorMode.ALIVE: {
        ConversationPhase.QUIET: BehaviorPolicy(1, 3_600, 2),
        ConversationPhase.COOLDOWN: BehaviorPolicy(1, 2_400, 4),
        ConversationPhase.WARMING_UP: BehaviorPolicy(1, 1_800, 6),
        ConversationPhase.ACTIVE: BehaviorPolicy(2, 900, 10),
        ConversationPhase.PEAK: BehaviorPolicy(1, 2_700, 16),
    },
    BehaviorMode.ACTIVE: {
        ConversationPhase.QUIET: BehaviorPolicy(1, 2_700, 1),
        ConversationPhase.COOLDOWN: BehaviorPolicy(1, 1_800, 3),
        ConversationPhase.WARMING_UP: BehaviorPolicy(2, 1_200, 4),
        ConversationPhase.ACTIVE: BehaviorPolicy(2, 600, 8),
        ConversationPhase.PEAK: BehaviorPolicy(1, 1_800, 12),
    },
}


def behavior_policy(mode: BehaviorMode) -> BehaviorPolicy:
    return _POLICIES.get(mode, _POLICIES[BehaviorMode.ALIVE])


def _has_long_horizon(activity: ActivitySnapshot) -> bool:
    return activity.messages_60m is not None and activity.messages_120m is not None


def adaptive_presence_policy(
    mode: BehaviorMode,
    phase: ConversationPhase,
    activity: ActivitySnapshot,
) -> BehaviorPolicy:
    """Return a topic-local action budget that follows real conversation tempo."""
    if not _has_long_horizon(activity):
        return behavior_policy(mode)

    policies = _ADAPTIVE_POLICIES.get(mode, _ADAPTIVE_POLICIES[BehaviorMode.ALIVE])
    effective_phase = phase
    if phase is ConversationPhase.QUIET and (
        int(activity.messages_60m or 0) >= 12
        or int(activity.active_users_60m or 0) >= 4
    ):
        effective_phase = ConversationPhase.COOLDOWN
    return policies[effective_phase]


def derive_phase(snapshot: ActivitySnapshot) -> ConversationPhase:
    """Classify one forum topic without consulting global chat activity."""
    if snapshot.messages_5m == 0 and snapshot.messages_15m <= 1:
        return ConversationPhase.QUIET
    if 1 <= snapshot.messages_5m <= 4 and snapshot.messages_previous_5m >= 8:
        return ConversationPhase.COOLDOWN
    if snapshot.messages_5m >= 12 and snapshot.active_users_5m >= 3:
        return ConversationPhase.PEAK
    if snapshot.messages_5m >= 5:
        return ConversationPhase.ACTIVE
    return ConversationPhase.WARMING_UP


def _clamp_unit(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _candidate_score(candidate: ActionCandidate, rng: random.Random) -> float:
    jitter = rng.uniform(0.0, 0.10)
    return (
        0.55 * _clamp_unit(candidate.relevance)
        + 0.35 * _clamp_unit(candidate.novelty)
        - 0.45 * _clamp_unit(candidate.annoyance_cost)
        + jitter
    )


def _recent_actions_30m(context: DecisionContext) -> tuple[EntertainmentActionRecord, ...]:
    cutoff = int(context.now) - 30 * 60
    return tuple(
        action
        for action in context.recent_actions
        if cutoff <= int(action.created_at) <= int(context.now)
    )


def _last_action(context: DecisionContext) -> EntertainmentActionRecord | None:
    eligible = [
        action
        for action in context.recent_actions
        if int(action.created_at) <= int(context.now)
    ]
    if not eligible:
        return None
    return max(eligible, key=lambda action: (int(action.created_at), int(action.id or 0)))


def presence_budget_allows(context: DecisionContext) -> bool:
    """Apply the shared text/media/greeting presence budget for one topic."""
    settings = context.settings
    if not settings.enabled or not settings.autonomous_text_enabled:
        return False
    if context.quiet_hours_active:
        return False

    policy = adaptive_presence_policy(settings.behavior_mode, context.phase, context.activity)
    if len(_recent_actions_30m(context)) >= policy.max_actions_30m:
        return False

    last_action = _last_action(context)
    if last_action is None:
        return True

    seconds_since_action = max(0, int(context.now) - int(last_action.created_at))
    if seconds_since_action < policy.min_gap_seconds:
        return False
    human_messages = int(context.human_messages_since_last_action)
    if human_messages <= 0 or human_messages < policy.min_human_messages_between:
        return False
    return True


def select_action(
    context: DecisionContext,
    candidates: list[ActionCandidate],
    *,
    rng: random.Random,
) -> ActionCandidate | None:
    """Select at most one safe autonomous action for a topic evaluation."""
    if not candidates or not presence_budget_allows(context):
        return None

    last_action = _last_action(context)
    dead_quiet_topic = (
        _has_long_horizon(context.activity)
        and context.phase is ConversationPhase.QUIET
        and int(context.activity.messages_120m or 0) <= 1
    )

    scored: list[tuple[float, int, ActionCandidate]] = []
    for index, candidate in enumerate(candidates):
        if dead_quiet_topic and not candidate.is_direct:
            continue
        if context.phase is ConversationPhase.PEAK and not candidate.is_direct:
            continue
        if (
            last_action is not None
            and candidate.action_type is last_action.action_type
            and not candidate.is_direct
        ):
            continue

        score = _candidate_score(candidate, rng)
        if score < 0.45:
            continue
        scored.append((score, -index, candidate))

    if not scored:
        return None
    return max(scored, key=lambda item: (item[0], item[1]))[2]


__all__ = [
    "ActionCandidate",
    "BehaviorPolicy",
    "ConversationPhase",
    "DecisionContext",
    "adaptive_presence_policy",
    "behavior_policy",
    "derive_phase",
    "presence_budget_allows",
    "select_action",
]

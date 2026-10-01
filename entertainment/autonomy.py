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
    BehaviorMode.CALM: BehaviorPolicy(
        max_actions_30m=1,
        min_gap_seconds=900,
        min_human_messages_between=8,
    ),
    BehaviorMode.ALIVE: BehaviorPolicy(
        max_actions_30m=2,
        min_gap_seconds=360,
        min_human_messages_between=4,
    ),
    BehaviorMode.ACTIVE: BehaviorPolicy(
        max_actions_30m=3,
        min_gap_seconds=180,
        min_human_messages_between=2,
    ),
}


def behavior_policy(mode: BehaviorMode) -> BehaviorPolicy:
    return _POLICIES.get(mode, _POLICIES[BehaviorMode.ALIVE])


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
    return max(
        eligible,
        key=lambda action: (int(action.created_at), int(action.id or 0)),
    )


def select_action(
    context: DecisionContext,
    candidates: list[ActionCandidate],
    *,
    rng: random.Random,
) -> ActionCandidate | None:
    """Select at most one safe autonomous action for a topic evaluation."""
    settings = context.settings
    if not settings.enabled or not settings.autonomous_text_enabled:
        return None
    if context.quiet_hours_active:
        return None
    if not candidates:
        return None

    policy = behavior_policy(settings.behavior_mode)
    recent_30m = _recent_actions_30m(context)
    if len(recent_30m) >= policy.max_actions_30m:
        return None

    last_action = _last_action(context)
    if last_action is not None:
        seconds_since_action = int(context.now) - int(last_action.created_at)
        if seconds_since_action < policy.min_gap_seconds:
            return None
        if context.human_messages_since_last_action <= 0:
            return None
        if context.human_messages_since_last_action < policy.min_human_messages_between:
            return None

    scored: list[tuple[float, int, ActionCandidate]] = []
    for index, candidate in enumerate(candidates):
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
    "behavior_policy",
    "derive_phase",
    "select_action",
]

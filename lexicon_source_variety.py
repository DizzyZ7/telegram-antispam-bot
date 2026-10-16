"""Pick varied, playable Lexicon sources without weakening the global cycle.

Only compiled, playable candidates are passed here. This is a pure selector:
no dictionary scans, Telegram calls, SQLite writes or network access.
"""

from __future__ import annotations

import random
from collections import Counter
from collections.abc import Mapping, Sequence
from itertools import takewhile
from typing import Protocol

from lexicon_source_quality import source_tier

RECENT_SOURCE_WINDOW = 8
FAMILY_PREFIX_LENGTH = 6
TIER_TARGETS = {"classic": 0.25, "rich": 0.50, "legendary": 0.25}


class WeightedChooser(Protocol):
    def choices(
        self, population: Sequence[str], weights: Sequence[float], *, k: int
    ) -> list[str]: ...


def _shared_prefix_length(a: str, b: str) -> int:
    return sum(1 for _ in takewhile(lambda pair: pair[0] == pair[1], zip(a, b)))


def _letter_overlap(a: Counter[str], b: Counter[str]) -> float:
    """Multiset Jaccard similarity: distinguish similar-looking anagrams."""
    shared = sum((a & b).values())
    combined = sum((a | b).values())
    return shared / combined if combined else 0.0


def choose_varied_source(
    available: Sequence[str],
    candidate_map: Mapping[str, set[str]],
    recent_sources: Sequence[str],
    *,
    rng: WeightedChooser | None = None,
) -> str:
    """Choose from available non-repeating sources with more variety.

    The database owns availability / cycle uniqueness. Here we avoid adjacent
    word families when alternatives exist and softly rebalance source tiers
    and near-anagrams across the latest eight claimed rounds.
    """
    if not available:
        raise ValueError("No available Lexicon starter words")

    if len(available) == 1:
        return available[0]

    recent = tuple(recent_sources[:RECENT_SOURCE_WINDOW])
    pool = list(available)
    if recent:
        alternatives = [
            word for word in pool
            if _shared_prefix_length(word, recent[0]) < FAMILY_PREFIX_LENGTH
        ]
        if alternatives:
            pool = alternatives

    recent_tiers = Counter(
        source_tier(len(candidate_map[word]))
        for word in recent
        if word in candidate_map
    )
    known_rounds = sum(recent_tiers.values())
    recent_bags = tuple(Counter(word) for word in recent)
    weights: list[float] = []

    for word in pool:
        tier = source_tier(len(candidate_map[word]))
        # An underrepresented tier gets a bigger weight; no tiers are banned.
        tier_weight = (
            (1.0 + known_rounds * TIER_TARGETS[tier])
            / (1.0 + recent_tiers[tier])
        )
        bag = Counter(word)
        novelty = 1.0
        for index, previous in enumerate(recent):
            if _shared_prefix_length(word, previous) >= FAMILY_PREFIX_LENGTH:
                novelty *= 0.18 if index < 2 else 0.65
            elif _letter_overlap(bag, recent_bags[index]) >= 0.82:
                novelty *= 0.45 if index < 2 else 0.80

        weights.append(max(0.01, tier_weight * novelty))

    random_source = rng if rng is not None else random
    return random_source.choices(pool, weights, k=1)[0]


__all__ = ["choose_varied_source", "RECENT_SOURCE_WINDOW"]

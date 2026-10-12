"""Choose varied, genuinely playable Lexicon source words.

Selection happens at startup, never on a Telegram guess. The input consists
only of already validated source words and their admissible answer sets.
Pure and deterministic: no DB migration, runtime network request or RNG.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

SourceCandidate = tuple[str, set[str]]


def source_quality(source_word: str, allowed_words: set[str]) -> float:
    """Balanced quality: breadth + enough useful medium/long discoveries.

    Cap the raw total so a giant source with hundreds of nearly identical
    short answers doesn't crowd out more interesting medium-sized rounds.
    """
    lengths = Counter(len(word) for word in allowed_words)
    short = sum(n for length, n in lengths.items() if 4 <= length <= 5)
    medium = sum(n for length, n in lengths.items() if 6 <= length <= 8)
    long = sum(n for length, n in lengths.items() if length >= 9)
    distinct_letters = len(set(source_word))
    return round(
        min(len(allowed_words), 155) * 0.65
        + min(short, 40) * 0.5
        + min(medium, 40) * 1.3
        + min(long, 25) * 2.0
        + min(distinct_letters, 15) * 1.3,
        3,
    )


def source_tier(solution_count: int) -> str:
    if solution_count >= 150:
        return "legendary"
    if solution_count >= 75:
        return "rich"
    return "classic"


def select_quality_sources(
    candidates: Iterable[SourceCandidate], *, limit: int = 2100,
) -> list[SourceCandidate]:
    """Tier-balanced shortlist without duplicate letter bags.

    Each tier gets reserved room where available. Unfilled space is used by
    the highest-quality remaining candidates from any tier, so the result
    never artificially shrinks when one difficulty band is scarce.
    """
    if limit <= 0:
        return []

    ranked: dict[str, list[SourceCandidate]] = {
        "classic": [], "rich": [], "legendary": [],
    }
    seen_bags: set[tuple[tuple[str, int], ...]] = set()
    scored = sorted(
        ((source_quality(source, answers), source, set(answers))
         for source, answers in candidates if source and answers),
        key=lambda item: (-item[0], -len(item[2]), item[1]),
    )
    for _quality, word, words in scored:
        bag = tuple(sorted(Counter(word).items()))
        if bag in seen_bags:
            continue
        seen_bags.add(bag)
        ranked[source_tier(len(words))].append((word, words))

    reserved = (
        ("classic", 0.20),
        ("rich", 0.50),
        ("legendary", 0.30),
    )
    selected: list[SourceCandidate] = []
    taken: set[str] = set()
    for tier, ratio in reserved:
        count = min(len(ranked[tier]), int(limit * ratio))
        for word, words in ranked[tier][:count]:
            selected.append((word, words))
            taken.add(word)

    if len(selected) < limit:
        remaining = [
            (word, words)
            for tier in ranked.values()
            for word, words in tier
            if word not in taken
        ]
        remaining.sort(key=lambda item: (-source_quality(*item), -len(item[1]), item[0]))
        selected.extend(remaining[:limit - len(selected)])

    # Stable ordering facilitates reproducible diagnostics; the persistent
    # source cycle itself still randomly chooses unused candidates per topic.
    return sorted(selected, key=lambda item: (-source_quality(*item), item[0]))


__all__ = ["select_quality_sources", "source_quality", "source_tier"]

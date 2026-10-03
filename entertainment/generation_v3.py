"""Chat-only Entertainment Generation v3.

The engine synthesizes from the bounded same-topic Culture Memory snapshot it
is given. It has no loaders for external corpora and intentionally fails closed
when there is not enough distinct chat material for safe composition.
"""

from __future__ import annotations

import random
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping

from .language import TopicAnchor, analyze_token, build_topic_anchor

_WORD_RE = re.compile(
    r"/?[A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9_'’-]*",
    re.UNICODE,
)
_BOUNDARY_WORDS = frozenset(
    {"и", "а", "но", "или", "что", "если", "когда", "потом", "зато", "чтобы", "пусть"}
)
_MIN_WORDS = 4
_MAX_WORDS = 24
_DEFAULT_CANDIDATES = 48
_MAX_RAW_CANDIDATES = 160


def _word_tokens(text: str) -> list[str]:
    return [token.lstrip("/").casefold() for token in _WORD_RE.findall(str(text))]


def normalize_source_text(text: str) -> str:
    """Normalize a source identity independently from weighting/repetition."""

    return " ".join(_word_tokens(text))


def distinct_source_identities(messages: Iterable[str]) -> set[str]:
    return {
        normalized
        for message in messages
        if (normalized := normalize_source_text(message))
    }


def longest_contiguous_word_overlap(candidate: str, source: str) -> int:
    """Return the longest contiguous word run shared by candidate and source."""

    left = _word_tokens(candidate)
    right = _word_tokens(source)
    if not left or not right:
        return 0

    previous = [0] * (len(right) + 1)
    best = 0
    for left_token in left:
        current = [0] * (len(right) + 1)
        for index, right_token in enumerate(right, start=1):
            if left_token == right_token:
                current[index] = previous[index - 1] + 1
                best = max(best, current[index])
        previous = current
    return best


def has_repeated_ngram(text: str, *, size: int = 3) -> bool:
    words = _word_tokens(text)
    if size <= 0 or len(words) < size:
        return False
    grams = list(zip(*(words[offset:] for offset in range(size))))
    return len(grams) != len(set(grams))


class GenerationMode(str, Enum):
    AUTONOMOUS = "autonomous"
    DIRECT_REPLY = "direct_reply"


@dataclass(frozen=True, slots=True)
class GenerationRequest:
    source_messages: list[str]
    context_messages: list[str]
    trigger_text: str | None
    mode: GenerationMode
    recent_bot_outputs: list[str]


@dataclass(frozen=True, slots=True)
class GenerationResult:
    text: str
    engine: str
    score: float
    candidate_count: int
    rejection_counts: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class TransitionModel:
    indexes: Mapping[int, Mapping[tuple[str, ...], tuple[str, ...]]]

    def next_options(self, history: Iterable[str]) -> tuple[int, tuple[str, ...]]:
        normalized = [str(token).lstrip("/").casefold() for token in history]
        for order in range(min(5, len(normalized)), 0, -1):
            key = tuple(normalized[-order:])
            options = self.indexes.get(order, {}).get(key, ())
            if options:
                return order, options
        return 0, ()


def build_transition_model(messages: Iterable[str]) -> TransitionModel:
    mutable: dict[int, dict[tuple[str, ...], list[str]]] = {
        order: defaultdict(list) for order in range(1, 6)
    }
    for message in messages:
        words = _word_tokens(message)
        for index in range(1, len(words)):
            next_token = words[index]
            for order in range(1, min(5, index) + 1):
                prefix = tuple(words[index - order : index])
                mutable[order][prefix].append(next_token)

    frozen: dict[int, dict[tuple[str, ...], tuple[str, ...]]] = {}
    for order, entries in mutable.items():
        frozen[order] = {
            prefix: tuple(options)
            for prefix, options in entries.items()
        }
    return TransitionModel(indexes=frozen)


@dataclass(frozen=True, slots=True)
class _SourceRecord:
    identity: str
    words: tuple[str, ...]
    weight: int


def _source_records(messages: Iterable[str]) -> list[_SourceRecord]:
    counts = Counter(
        normalized
        for message in messages
        if (normalized := normalize_source_text(message))
    )
    return [
        _SourceRecord(identity=identity, words=tuple(identity.split()), weight=count)
        for identity, count in counts.items()
        if len(identity.split()) >= 2
    ]


def _bridge_pairs(left: _SourceRecord, right: _SourceRecord) -> list[tuple[int, int]]:
    pairs: list[tuple[int, int]] = []
    left_analysis = [analyze_token(token) for token in left.words]
    right_analysis = [analyze_token(token) for token in right.words]
    for left_index, left_token in enumerate(left_analysis):
        for right_index, right_token in enumerate(right_analysis):
            same_surface = left_token.normalized == right_token.normalized
            same_lemma = bool(left_token.lemma) and left_token.lemma == right_token.lemma
            same_boundary = (
                left_token.normalized in _BOUNDARY_WORDS
                and left_token.normalized == right_token.normalized
            )
            if same_surface or same_lemma or same_boundary:
                pairs.append((left_index, right_index))
    return pairs


def _render(words: Iterable[str]) -> str:
    text = " ".join(words).strip()
    if not text:
        return ""
    return text[0].upper() + text[1:] + "."


def _supporting_source_count(candidate: str, records: Iterable[_SourceRecord]) -> int:
    # Two consecutive words are enough to prove that a source contributed a
    # phrase fragment; identities are already de-duplicated above.
    return sum(
        longest_contiguous_word_overlap(candidate, record.identity) >= 2
        for record in records
    )


def _supported_trigram_ratio(candidate: str, records: Iterable[_SourceRecord]) -> float:
    words = _word_tokens(candidate)
    grams = list(zip(words, words[1:], words[2:]))
    if not grams:
        return 0.0
    supported: set[tuple[str, str, str]] = set()
    for record in records:
        source = list(record.words)
        supported.update(zip(source, source[1:], source[2:]))
    return sum(gram in supported for gram in grams) / len(grams)


def _morphology_signal(candidate: str) -> float:
    words = _word_tokens(candidate)
    if not words:
        return 0.0
    confident = sum(analyze_token(word).morph_confident for word in words)
    # Unknown slang/English is never rejected or heavily penalized. Known
    # morphology provides only a small positive reranking signal.
    return confident / len(words)


def _trigger_coverage(candidate: str, anchor: TopicAnchor) -> int:
    if not anchor.trigger_terms:
        return 0
    lemmas = {analyze_token(word).lemma for word in _word_tokens(candidate)}
    return len(lemmas & anchor.trigger_terms)


def _topic_relevance(candidate: str, anchor: TopicAnchor) -> float:
    return anchor.relevance(_word_tokens(candidate))


def _rejection_reason(
    candidate: str,
    *,
    records: list[_SourceRecord],
    recent_outputs: set[str],
) -> str | None:
    normalized = normalize_source_text(candidate)
    words = normalized.split()
    if len(words) < _MIN_WORDS:
        return "too_short"
    if len(words) > _MAX_WORDS:
        return "too_long"
    if normalized in {record.identity for record in records}:
        return "exact_source"
    if normalized in recent_outputs:
        return "recent_output"
    if has_repeated_ngram(candidate, size=3):
        return "repeated_trigram"

    for record in records:
        overlap = longest_contiguous_word_overlap(candidate, record.identity)
        if overlap > 6:
            return "source_overlap_words"
        if words and (overlap / len(words)) > 0.70:
            return "source_overlap_ratio"

    if len(words) >= 6 and _supporting_source_count(candidate, records) < 2:
        return "single_source"
    return None


def _score_candidate(
    candidate: str,
    *,
    anchor: TopicAnchor,
    records: list[_SourceRecord],
    mode: GenerationMode,
    rng: random.Random,
) -> float:
    words = _word_tokens(candidate)
    topic = anchor.relevance(words)
    support = _supported_trigram_ratio(candidate, records)
    source_count = min(_supporting_source_count(candidate, records), 3) / 3.0
    morphology = _morphology_signal(candidate)
    length_score = 1.0 - min(abs(len(words) - 9) / 12.0, 1.0)

    trigger_bonus = 0.0
    if mode is GenerationMode.DIRECT_REPLY and anchor.trigger_terms:
        trigger_bonus = min(
            _trigger_coverage(candidate, anchor) / len(anchor.trigger_terms),
            1.0,
        )

    # Current-topic fit intentionally dominates historical phrase frequency.
    return (
        5.0 * topic
        + 2.0 * trigger_bonus
        + 1.6 * support
        + 0.6 * source_count
        + 0.25 * morphology
        + 0.35 * length_score
        + rng.random() * 0.08
    )


class GenerationV3:
    """Bounded local synthesizer over one already-isolated topic snapshot."""

    def __init__(self, *, candidate_target: int = _DEFAULT_CANDIDATES) -> None:
        self.candidate_target = max(32, min(int(candidate_target), 64))

    @staticmethod
    def _weighted_record_choice(
        records: list[_SourceRecord],
        anchor: TopicAnchor,
        rng: random.Random,
    ) -> _SourceRecord:
        weights = []
        for record in records:
            topical = anchor.relevance(record.words)
            # Preserve Culture weighting weakly, but never let historical
            # repetition outrank current-topic relevance.
            weights.append(1.0 + 5.0 * topical + min(record.weight, 5) * 0.08)
        return rng.choices(records, weights=weights, k=1)[0]

    def _crossover_candidates(
        self,
        records: list[_SourceRecord],
        anchor: TopicAnchor,
        rng: random.Random,
    ) -> list[str]:
        output: list[str] = []
        attempts = min(_MAX_RAW_CANDIDATES, self.candidate_target * 3)
        for _ in range(attempts):
            left = self._weighted_record_choice(records, anchor, rng)
            alternatives = [record for record in records if record.identity != left.identity]
            if not alternatives:
                break
            right = self._weighted_record_choice(alternatives, anchor, rng)
            bridges = _bridge_pairs(left, right)
            if not bridges:
                continue
            left_index, right_index = rng.choice(bridges)
            words = [*left.words[: left_index + 1], *right.words[right_index + 1 :]]
            if _MIN_WORDS <= len(words) <= _MAX_WORDS:
                output.append(_render(words))
            if len(output) >= self.candidate_target * 2:
                break
        return output

    def _backoff_candidates(
        self,
        records: list[_SourceRecord],
        model: TransitionModel,
        anchor: TopicAnchor,
        rng: random.Random,
    ) -> list[str]:
        output: list[str] = []
        attempts = self.candidate_target * 2
        for _ in range(attempts):
            source = self._weighted_record_choice(records, anchor, rng)
            start_size = min(len(source.words), rng.choice((2, 3)))
            words = list(source.words[:start_size])
            switched = False

            for _step in range(_MAX_WORDS - len(words)):
                if words[-1] in _BOUNDARY_WORDS and rng.random() < 0.78:
                    order, options = model.next_options([words[-1]])
                    switched = switched or bool(options)
                elif rng.random() < 0.14:
                    order, options = model.next_options([words[-1]])
                    switched = switched or bool(options)
                else:
                    order, options = model.next_options(words)
                if not options:
                    break

                counts = Counter(options)
                choices = list(counts)
                weights = [float(counts[token]) for token in choices]
                next_token = rng.choices(choices, weights=weights, k=1)[0]
                words.append(next_token)

                if len(words) >= 7 and switched and rng.random() < 0.28:
                    break
                if len(words) >= 12 and rng.random() < 0.45:
                    break

            if _MIN_WORDS <= len(words) <= _MAX_WORDS:
                output.append(_render(words))
        return output

    def generate(
        self,
        request: GenerationRequest,
        *,
        rng: random.Random,
    ) -> GenerationResult | None:
        records = _source_records(request.source_messages)
        # A single normalized source, even repeated many times by Culture
        # weighting, cannot prove composition. Fail closed.
        if len(records) < 2:
            return None

        direct = request.mode is GenerationMode.DIRECT_REPLY
        anchor = build_topic_anchor(
            request.context_messages,
            trigger_text=request.trigger_text,
            direct_reply=direct,
        )
        model = build_transition_model(record.identity for record in records)
        raw_candidates = self._crossover_candidates(records, anchor, rng)
        raw_candidates.extend(self._backoff_candidates(records, model, anchor, rng))

        recent_outputs = {
            normalized
            for text in request.recent_bot_outputs
            if (normalized := normalize_source_text(text))
        }
        rejections: Counter[str] = Counter()
        accepted: dict[str, tuple[float, str]] = {}

        for candidate in raw_candidates[:_MAX_RAW_CANDIDATES]:
            reason = _rejection_reason(
                candidate,
                records=records,
                recent_outputs=recent_outputs,
            )
            if reason is not None:
                rejections[reason] += 1
                continue

            normalized = normalize_source_text(candidate)
            score = _score_candidate(
                candidate,
                anchor=anchor,
                records=records,
                mode=request.mode,
                rng=rng,
            )
            previous = accepted.get(normalized)
            if previous is None or score > previous[0]:
                accepted[normalized] = (score, candidate)

        if not accepted:
            return None

        ranked_pool = list(accepted.values())
        if direct and anchor.trigger_terms:
            maximum_coverage = max(
                _trigger_coverage(candidate, anchor)
                for _score, candidate in ranked_pool
            )
            if maximum_coverage > 0:
                ranked_pool = [
                    item
                    for item in ranked_pool
                    if _trigger_coverage(item[1], anchor) == maximum_coverage
                ]

            maximum_topic_relevance = max(
                _topic_relevance(candidate, anchor)
                for _score, candidate in ranked_pool
            )
            topical_floor = maximum_topic_relevance * 0.95
            ranked_pool = [
                item
                for item in ranked_pool
                if _topic_relevance(item[1], anchor) >= topical_floor
            ]

        maximum_support = max(
            _supported_trigram_ratio(candidate, records)
            for _score, candidate in ranked_pool
        )
        if maximum_support > 0.0:
            support_floor = max(0.0, maximum_support - 0.01)
            ranked_pool = [
                item
                for item in ranked_pool
                if _supported_trigram_ratio(item[1], records) >= support_floor
            ]

        ranked = sorted(ranked_pool, key=lambda item: (-item[0], item[1]))
        high_quality = ranked[: min(8, len(ranked))]
        rank_weights = [1.0 / ((index + 1) ** 0.70) for index in range(len(high_quality))]
        score, text = rng.choices(high_quality, weights=rank_weights, k=1)[0]
        return GenerationResult(
            text=text,
            engine="v3",
            score=round(score, 6),
            candidate_count=len(accepted),
            rejection_counts=dict(sorted(rejections.items())),
        )


__all__ = [
    "GenerationMode",
    "GenerationRequest",
    "GenerationResult",
    "GenerationV3",
    "TransitionModel",
    "build_transition_model",
    "distinct_source_identities",
    "has_repeated_ngram",
    "longest_contiguous_word_overlap",
    "normalize_source_text",
]

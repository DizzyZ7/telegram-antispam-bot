"""Privacy-safe Generation v3 production telemetry.

The collector intentionally stores counters only. It never stores generated text,
source messages, trigger text, context messages or user identifiers.
"""

from __future__ import annotations

from collections import Counter, OrderedDict, deque
from dataclasses import dataclass
from typing import Iterable

from .generation_v3 import GenerationResult
from .models import EntertainmentActionRecord

_SCORE_BUCKETS = frozenset({"none", "low", "medium", "high", "very_high"})
_DEFAULT_SCOPE_LIMIT = 256
_RECENT_WINDOW_SIZE = 32


def score_bucket(score: float) -> str:
    if score < 2.0:
        return "low"
    if score < 4.0:
        return "medium"
    if score < 6.0:
        return "high"
    return "very_high"


def _safe_count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return int(value)


def _safe_rejections(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, int] = {}
    for key, raw_count in value.items():
        if not isinstance(key, str) or not key:
            continue
        count = _safe_count(raw_count)
        if count is None:
            continue
        result[key] = count
    return result


@dataclass(frozen=True, slots=True)
class GenerationMetricsSnapshot:
    attempts: int
    successes: int
    no_output: int
    engine_counts: dict[str, int]
    mode_counts: dict[str, int]
    score_buckets: dict[str, int]
    rejection_counts: dict[str, int]
    candidate_total: int
    candidate_samples: int

    @property
    def average_candidate_count(self) -> float:
        if self.candidate_samples <= 0:
            return 0.0
        return self.candidate_total / self.candidate_samples

    @property
    def success_rate(self) -> float:
        if self.attempts <= 0:
            return 0.0
        return self.successes / self.attempts


def _combine_snapshots(
    snapshots: Iterable[GenerationMetricsSnapshot],
) -> GenerationMetricsSnapshot:
    attempts = 0
    successes = 0
    no_output = 0
    engine_counts: Counter[str] = Counter()
    mode_counts: Counter[str] = Counter()
    score_buckets: Counter[str] = Counter()
    rejection_counts: Counter[str] = Counter()
    candidate_total = 0
    candidate_samples = 0

    for snapshot in snapshots:
        attempts += int(snapshot.attempts)
        successes += int(snapshot.successes)
        no_output += int(snapshot.no_output)
        engine_counts.update(snapshot.engine_counts)
        mode_counts.update(snapshot.mode_counts)
        score_buckets.update(snapshot.score_buckets)
        rejection_counts.update(snapshot.rejection_counts)
        candidate_total += int(snapshot.candidate_total)
        candidate_samples += int(snapshot.candidate_samples)

    return GenerationMetricsSnapshot(
        attempts=attempts,
        successes=successes,
        no_output=no_output,
        engine_counts=dict(engine_counts),
        mode_counts=dict(mode_counts),
        score_buckets=dict(score_buckets),
        rejection_counts=dict(rejection_counts),
        candidate_total=candidate_total,
        candidate_samples=candidate_samples,
    )


class GenerationMetrics:
    """Process-local aggregate counters for generation attempts."""

    __slots__ = (
        "_attempts",
        "_successes",
        "_no_output",
        "_engine_counts",
        "_mode_counts",
        "_score_buckets",
        "_rejection_counts",
        "_candidate_total",
        "_candidate_samples",
    )

    def __init__(self) -> None:
        self._attempts = 0
        self._successes = 0
        self._no_output = 0
        self._engine_counts: Counter[str] = Counter()
        self._mode_counts: Counter[str] = Counter()
        self._score_buckets: Counter[str] = Counter()
        self._rejection_counts: Counter[str] = Counter()
        self._candidate_total = 0
        self._candidate_samples = 0

    def record(
        self,
        *,
        mode: str,
        engine: str,
        result: GenerationResult | None,
    ) -> None:
        if not isinstance(mode, str) or not mode:
            return
        if not isinstance(engine, str) or not engine:
            return

        self._attempts += 1
        self._engine_counts[engine] += 1
        self._mode_counts[mode] += 1

        if result is None:
            self._no_output += 1
            return

        self._successes += 1
        self._score_buckets[score_bucket(float(result.score))] += 1
        candidate_count = _safe_count(result.candidate_count)
        if candidate_count is not None:
            self._candidate_total += candidate_count
            self._candidate_samples += 1
        self._rejection_counts.update(_safe_rejections(result.rejection_counts))

    def _record_persisted(
        self,
        *,
        engine: str,
        mode: str,
        candidate_count: int | None,
        bucket: str | None,
        rejections: dict[str, int],
    ) -> None:
        self._attempts += 1
        self._successes += 1
        self._engine_counts[engine] += 1
        self._mode_counts[mode] += 1
        if candidate_count is not None:
            self._candidate_total += candidate_count
            self._candidate_samples += 1
        if bucket in _SCORE_BUCKETS:
            self._score_buckets[bucket] += 1
        self._rejection_counts.update(rejections)

    def snapshot(self) -> GenerationMetricsSnapshot:
        return GenerationMetricsSnapshot(
            attempts=self._attempts,
            successes=self._successes,
            no_output=self._no_output,
            engine_counts=dict(self._engine_counts),
            mode_counts=dict(self._mode_counts),
            score_buckets=dict(self._score_buckets),
            rejection_counts=dict(self._rejection_counts),
            candidate_total=self._candidate_total,
            candidate_samples=self._candidate_samples,
        )


class ScopedGenerationMetrics:
    """Bounded process-local metrics isolated by chat/topic scope."""

    __slots__ = ("_aggregate", "_scopes", "_recent", "_max_scopes")

    def __init__(self, *, max_scopes: int = _DEFAULT_SCOPE_LIMIT) -> None:
        self._aggregate = GenerationMetrics()
        self._scopes: OrderedDict[tuple[int, int], GenerationMetrics] = OrderedDict()
        self._recent: dict[tuple[int, int], deque[GenerationMetricsSnapshot]] = {}
        self._max_scopes = max(1, int(max_scopes))

    def record(
        self,
        *,
        mode: str,
        engine: str,
        result: GenerationResult | None,
    ) -> None:
        """Record legacy/unscoped diagnostics in the aggregate only."""
        self._aggregate.record(mode=mode, engine=engine, result=result)

    def record_for(
        self,
        chat_id: int,
        topic_id: int,
        *,
        mode: str,
        engine: str,
        result: GenerationResult | None,
    ) -> None:
        """Record one attempt in aggregate, lifetime scope and recent window."""
        key = (int(chat_id), int(topic_id))
        metrics = self._scopes.get(key)
        if metrics is None:
            metrics = GenerationMetrics()
            self._scopes[key] = metrics
            self._recent[key] = deque(maxlen=_RECENT_WINDOW_SIZE)
            while len(self._scopes) > self._max_scopes:
                evicted_key, _ = self._scopes.popitem(last=False)
                self._recent.pop(evicted_key, None)
        else:
            self._scopes.move_to_end(key)

        self._aggregate.record(mode=mode, engine=engine, result=result)
        metrics.record(mode=mode, engine=engine, result=result)

        sample_metrics = GenerationMetrics()
        sample_metrics.record(mode=mode, engine=engine, result=result)
        sample = sample_metrics.snapshot()
        if sample.attempts:
            self._recent[key].append(sample)

    def snapshot(self) -> GenerationMetricsSnapshot:
        return self._aggregate.snapshot()

    def snapshot_for(self, chat_id: int, topic_id: int) -> GenerationMetricsSnapshot:
        key = (int(chat_id), int(topic_id))
        metrics = self._scopes.get(key)
        if metrics is None:
            return GenerationMetrics().snapshot()
        self._scopes.move_to_end(key)
        return metrics.snapshot()

    def recent_snapshot_for(self, chat_id: int, topic_id: int) -> GenerationMetricsSnapshot:
        """Return privacy-safe counters for at most the last 32 scoped attempts."""
        key = (int(chat_id), int(topic_id))
        samples = self._recent.get(key)
        if samples is None:
            return GenerationMetrics().snapshot()
        if key in self._scopes:
            self._scopes.move_to_end(key)
        return _combine_snapshots(samples)


def aggregate_generation_actions(
    actions: Iterable[EntertainmentActionRecord],
) -> GenerationMetricsSnapshot:
    """Aggregate persisted safe diagnostics and ignore unrelated/malformed rows."""
    metrics = GenerationMetrics()
    for action in actions:
        metadata = action.metadata
        engine = metadata.get("generation_engine")
        mode = metadata.get("generation_mode")
        if not isinstance(engine, str) or not engine:
            continue
        if not isinstance(mode, str) or not mode:
            continue

        candidate_count = _safe_count(metadata.get("generation_candidate_count"))
        raw_bucket = metadata.get("generation_score_bucket")
        bucket = raw_bucket if isinstance(raw_bucket, str) else None
        rejections = _safe_rejections(metadata.get("generation_rejections"))
        metrics._record_persisted(
            engine=engine,
            mode=mode,
            candidate_count=candidate_count,
            bucket=bucket,
            rejections=rejections,
        )
    return metrics.snapshot()


__all__ = [
    "GenerationMetrics",
    "GenerationMetricsSnapshot",
    "ScopedGenerationMetrics",
    "aggregate_generation_actions",
    "score_bucket",
]

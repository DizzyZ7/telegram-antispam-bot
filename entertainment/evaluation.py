"""Deterministic offline quality metrics for Entertainment generation.

The evaluator consumes only caller-provided synthetic/anonymized fixtures. It
never loads Telegram history, files, external corpora, models or network data.
"""

from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass
from typing import Iterable, Sequence

from .generation import generate_text
from .generation_v3 import (
    GenerationRequest,
    longest_contiguous_word_overlap,
    normalize_source_text,
)
from .language import build_topic_anchor

_WORD_RE = re.compile(
    r"/?[A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9_'’-]*",
    re.UNICODE,
)


def _words(text: str) -> list[str]:
    return [token.lstrip("/").casefold() for token in _WORD_RE.findall(str(text))]


def _nonempty(outputs: Iterable[str | None]) -> list[str]:
    return [str(output) for output in outputs if isinstance(output, str) and output.strip()]


def exact_replay_rate(outputs: Iterable[str | None], sources: Iterable[str]) -> float:
    generated = _nonempty(outputs)
    if not generated:
        return 0.0
    source_ids = {
        normalized
        for source in sources
        if (normalized := normalize_source_text(source))
    }
    replays = sum(normalize_source_text(output) in source_ids for output in generated)
    return replays / len(generated)


def longest_source_overlap_rate(
    outputs: Iterable[str | None],
    sources: Iterable[str],
) -> float:
    """Mean fraction of each generated line covered by its longest source run."""

    generated = _nonempty(outputs)
    source_list = [source for source in sources if normalize_source_text(source)]
    if not generated or not source_list:
        return 0.0

    ratios: list[float] = []
    for output in generated:
        word_count = max(1, len(_words(output)))
        longest = max(
            longest_contiguous_word_overlap(output, source)
            for source in source_list
        )
        ratios.append(longest / word_count)
    return sum(ratios) / len(ratios)


def unsafe_source_overlap_rate(
    outputs: Iterable[str | None],
    sources: Iterable[str],
) -> float:
    """Rate violating v3's max-6-words / max-70%-of-candidate copy boundary."""

    generated = _nonempty(outputs)
    source_list = [source for source in sources if normalize_source_text(source)]
    if not generated or not source_list:
        return 0.0

    unsafe = 0
    for output in generated:
        word_count = max(1, len(_words(output)))
        for source in source_list:
            overlap = longest_contiguous_word_overlap(output, source)
            if overlap > 6 or (overlap / word_count) > 0.70:
                unsafe += 1
                break
    return unsafe / len(generated)


def supported_ngram_ratio(
    outputs: Iterable[str | None],
    sources: Iterable[str],
    *,
    size: int = 3,
) -> float:
    if size <= 0:
        raise ValueError("size must be positive")

    source_grams: set[tuple[str, ...]] = set()
    for source in sources:
        words = _words(source)
        if len(words) < size:
            continue
        source_grams.update(
            tuple(words[index : index + size])
            for index in range(len(words) - size + 1)
        )

    supported = 0
    total = 0
    for output in _nonempty(outputs):
        words = _words(output)
        for index in range(max(0, len(words) - size + 1)):
            total += 1
            if tuple(words[index : index + size]) in source_grams:
                supported += 1
    if total == 0:
        return 0.0
    return supported / total


def topic_anchor_overlap(
    outputs: Iterable[str | None],
    context_messages: list[str],
    *,
    trigger_text: str | None,
    direct_reply: bool,
) -> float:
    generated = _nonempty(outputs)
    if not generated:
        return 0.0
    anchor = build_topic_anchor(
        list(context_messages),
        trigger_text=trigger_text,
        direct_reply=direct_reply,
    )
    scores = [anchor.relevance(_words(output)) for output in generated]
    return sum(scores) / len(scores)


def output_diversity(outputs: Iterable[str | None]) -> float:
    generated = _nonempty(outputs)
    if not generated:
        return 0.0
    identities = {
        normalized
        for output in generated
        if (normalized := normalize_source_text(output))
    }
    return len(identities) / len(generated)


def _percentile(values: Sequence[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    q = min(max(float(quantile), 0.0), 1.0)
    index = int(round((len(ordered) - 1) * q))
    return ordered[index]


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    engine: str
    outputs: tuple[str | None, ...]
    exact_replay_rate: float
    mean_source_overlap_rate: float
    unsafe_source_overlap_rate: float
    topic_anchor_overlap: float
    supported_ngram_ratio: float
    output_diversity: float
    no_output_rate: float
    latency_ms_mean: float
    latency_ms_p95: float


def benchmark_engine(
    request: GenerationRequest,
    *,
    engine: str,
    seeds: Iterable[int],
) -> BenchmarkResult:
    """Run one local engine on fixed seeds and summarize quality signals."""

    seed_list = [int(seed) for seed in seeds]
    outputs: list[str | None] = []
    latencies_ms: list[float] = []

    for seed in seed_list:
        started = time.perf_counter()
        result = generate_text(
            request,
            rng=random.Random(seed),
            engine=engine,
        )
        latencies_ms.append((time.perf_counter() - started) * 1000.0)
        outputs.append(result.text if result is not None else None)

    attempts = len(seed_list)
    no_output = sum(output is None for output in outputs)
    direct_reply = request.mode.value == "direct_reply"
    return BenchmarkResult(
        engine=str(engine),
        outputs=tuple(outputs),
        exact_replay_rate=exact_replay_rate(outputs, request.source_messages),
        mean_source_overlap_rate=longest_source_overlap_rate(
            outputs,
            request.source_messages,
        ),
        unsafe_source_overlap_rate=unsafe_source_overlap_rate(
            outputs,
            request.source_messages,
        ),
        topic_anchor_overlap=topic_anchor_overlap(
            outputs,
            request.context_messages,
            trigger_text=request.trigger_text,
            direct_reply=direct_reply,
        ),
        supported_ngram_ratio=supported_ngram_ratio(
            outputs,
            request.source_messages,
            size=3,
        ),
        output_diversity=output_diversity(outputs),
        no_output_rate=(no_output / attempts) if attempts else 0.0,
        latency_ms_mean=(sum(latencies_ms) / len(latencies_ms)) if latencies_ms else 0.0,
        latency_ms_p95=_percentile(latencies_ms, 0.95),
    )


__all__ = [
    "BenchmarkResult",
    "benchmark_engine",
    "exact_replay_rate",
    "longest_source_overlap_rate",
    "output_diversity",
    "supported_ngram_ratio",
    "topic_anchor_overlap",
    "unsafe_source_overlap_rate",
]

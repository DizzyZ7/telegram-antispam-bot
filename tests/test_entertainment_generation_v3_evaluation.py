from __future__ import annotations

import random
import unittest

from entertainment.evaluation import (
    benchmark_engine,
    exact_replay_rate,
    longest_source_overlap_rate,
    output_diversity,
    supported_ngram_ratio,
    topic_anchor_overlap,
)
from entertainment.generation_v3 import GenerationMode, GenerationRequest


SOURCES = [
    "автобус скоро приедет к вокзалу и заберет всех домой",
    "метро уже закрывается на ночь и город становится тихим",
    "автобус вечером идет через центр и люди ждут его у вокзала",
    "трамвай сворачивает после моста и город постепенно засыпает",
    "мы сегодня ждем автобус и потом сразу едем домой",
    "у вокзала шумно вечером и последние автобусы еще ходят",
]


def request() -> GenerationRequest:
    return GenerationRequest(
        source_messages=SOURCES * 5,
        context_messages=[
            "когда автобус будет у вокзала",
            "автобус уже скоро должен приехать",
        ],
        trigger_text="где наш автобус у вокзала",
        mode=GenerationMode.DIRECT_REPLY,
        recent_bot_outputs=["вчера уже шутили про метро"],
    )


class GenerationV3EvaluationTests(unittest.TestCase):
    def test_metric_helpers_detect_replay_overlap_support_and_diversity(self):
        outputs = [
            "автобус скоро приедет к вокзалу и люди едут домой",
            "у вокзала автобус стоит и город постепенно засыпает",
        ]
        self.assertEqual(exact_replay_rate([SOURCES[0]], SOURCES), 1.0)
        self.assertEqual(exact_replay_rate(outputs, SOURCES), 0.0)
        self.assertGreater(longest_source_overlap_rate(outputs, SOURCES), 0.0)
        self.assertGreater(supported_ngram_ratio(outputs, SOURCES, size=2), 0.5)
        self.assertGreater(
            topic_anchor_overlap(
                outputs,
                request().context_messages,
                trigger_text=request().trigger_text,
                direct_reply=True,
            ),
            0.0,
        )
        self.assertEqual(output_diversity(outputs), 1.0)

    def test_v3_offline_benchmark_is_deterministic_and_safe(self):
        seeds = [3, 7, 11, 19, 23, 31]
        first = benchmark_engine(request(), engine="v3", seeds=seeds)
        second = benchmark_engine(request(), engine="v3", seeds=seeds)

        self.assertEqual(first.outputs, second.outputs)
        self.assertEqual(first.exact_replay_rate, 0.0)
        self.assertEqual(first.unsafe_source_overlap_rate, 0.0)
        self.assertGreater(first.topic_anchor_overlap, 0.0)
        self.assertGreater(first.supported_ngram_ratio, 0.0)
        self.assertGreater(first.output_diversity, 0.0)
        self.assertLess(first.no_output_rate, 1.0)
        self.assertGreaterEqual(first.latency_ms_p95, 0.0)
        self.assertLess(first.latency_ms_p95, 5000.0)

    def test_v3_quality_gate_against_v2_on_same_anonymized_fixture(self):
        seeds = [2, 5, 13, 17, 29, 37, 41, 43]
        v2 = benchmark_engine(request(), engine="v2", seeds=seeds)
        v3 = benchmark_engine(request(), engine="v3", seeds=seeds)

        self.assertLessEqual(v3.exact_replay_rate, v2.exact_replay_rate)
        self.assertLessEqual(v3.unsafe_source_overlap_rate, v2.unsafe_source_overlap_rate)
        self.assertGreaterEqual(v3.topic_anchor_overlap, v2.topic_anchor_overlap)
        self.assertGreaterEqual(v3.supported_ngram_ratio, v2.supported_ngram_ratio)
        self.assertLess(v3.no_output_rate, 1.0)

    def test_short_single_source_corpus_fails_closed_instead_of_copying(self):
        tiny = GenerationRequest(
            source_messages=["Капец, вот: 3."] * 30,
            context_messages=["капец"],
            trigger_text=None,
            mode=GenerationMode.AUTONOMOUS,
            recent_bot_outputs=[],
        )
        result = benchmark_engine(tiny, engine="v3", seeds=[1, 2, 3])
        self.assertEqual(result.no_output_rate, 1.0)
        self.assertEqual(result.exact_replay_rate, 0.0)


if __name__ == "__main__":
    unittest.main()

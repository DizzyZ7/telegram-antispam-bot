from __future__ import annotations

import unittest

from entertainment.generation_metrics import ScopedGenerationMetrics
from entertainment.generation_v3 import GenerationResult


def result(text: str) -> GenerationResult:
    return GenerationResult(
        text=text,
        engine="v3",
        score=4.5,
        candidate_count=7,
        rejection_counts={"exact_source": 1},
    )


class ScopedGenerationMetricsTests(unittest.TestCase):
    def test_lru_evicts_least_recent_scope_without_losing_aggregate_totals(self):
        metrics = ScopedGenerationMetrics(max_scopes=2)

        metrics.record_for(1, 10, mode="direct_reply", engine="v3", result=result("SECRET A"))
        metrics.record_for(1, 11, mode="autonomous", engine="v3", result=None)

        # A status read makes topic 10 the most recently used scope.
        self.assertEqual(metrics.snapshot_for(1, 10).attempts, 1)

        metrics.record_for(2, 20, mode="autonomous", engine="v3", result=result("SECRET C"))

        self.assertEqual(metrics.snapshot_for(1, 10).attempts, 1)
        self.assertEqual(metrics.snapshot_for(1, 11).attempts, 0)
        self.assertEqual(metrics.snapshot_for(2, 20).attempts, 1)

        aggregate = metrics.snapshot()
        self.assertEqual(aggregate.attempts, 3)
        self.assertEqual(aggregate.successes, 2)
        self.assertEqual(aggregate.no_output, 1)

        # The collector stores counters only, never GenerationResult.text.
        self.assertNotIn("SECRET A", repr(metrics._scopes))
        self.assertNotIn("SECRET C", repr(metrics._scopes))

    def test_default_scope_cache_is_bounded_to_256_topics(self):
        metrics = ScopedGenerationMetrics()
        for topic_id in range(257):
            metrics.record_for(
                -1001,
                topic_id,
                mode="autonomous",
                engine="v3",
                result=None,
            )

        self.assertEqual(metrics.snapshot_for(-1001, 0).attempts, 0)
        self.assertEqual(metrics.snapshot_for(-1001, 256).attempts, 1)
        self.assertEqual(metrics.snapshot().attempts, 257)


if __name__ == "__main__":
    unittest.main()

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
        self.assertEqual(metrics.recent_snapshot_for(1, 10).attempts, 1)
        self.assertEqual(metrics.recent_snapshot_for(1, 11).attempts, 0)
        self.assertEqual(metrics.recent_snapshot_for(2, 20).attempts, 1)

        aggregate = metrics.snapshot()
        self.assertEqual(aggregate.attempts, 3)
        self.assertEqual(aggregate.successes, 2)
        self.assertEqual(aggregate.no_output, 1)

        # The collector stores counters only, never GenerationResult.text.
        self.assertNotIn("SECRET A", repr(metrics._scopes))
        self.assertNotIn("SECRET C", repr(metrics._scopes))
        self.assertNotIn("SECRET A", repr(metrics._recent))
        self.assertNotIn("SECRET C", repr(metrics._recent))

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
        self.assertEqual(metrics.recent_snapshot_for(-1001, 0).attempts, 0)
        self.assertEqual(metrics.snapshot_for(-1001, 256).attempts, 1)
        self.assertEqual(metrics.recent_snapshot_for(-1001, 256).attempts, 1)
        self.assertEqual(metrics.snapshot().attempts, 257)

    def test_recent_snapshot_keeps_only_last_32_attempts_without_truncating_live_totals(self):
        metrics = ScopedGenerationMetrics()

        for index in range(8):
            metrics.record_for(
                -1001,
                10,
                mode="direct_reply",
                engine="v3",
                result=result(f"SECRET SUCCESS {index}"),
            )
        for _ in range(32):
            metrics.record_for(
                -1001,
                10,
                mode="autonomous",
                engine="v3",
                result=None,
            )

        live = metrics.snapshot_for(-1001, 10)
        recent = metrics.recent_snapshot_for(-1001, 10)

        self.assertEqual((live.attempts, live.successes, live.no_output), (40, 8, 32))
        self.assertEqual((recent.attempts, recent.successes, recent.no_output), (32, 0, 32))
        self.assertEqual(recent.mode_counts, {"autonomous": 32})
        self.assertEqual(recent.engine_counts, {"v3": 32})
        self.assertNotIn("SECRET SUCCESS", repr(metrics))

    def test_recent_windows_are_isolated_by_chat_and_topic(self):
        metrics = ScopedGenerationMetrics()

        metrics.record_for(-1001, 10, mode="direct_reply", engine="v3", result=result("SECRET A"))
        metrics.record_for(-1001, 11, mode="autonomous", engine="v3", result=None)
        metrics.record_for(-2002, 10, mode="autonomous", engine="v2", result=result("SECRET B"))

        topic_10 = metrics.recent_snapshot_for(-1001, 10)
        topic_11 = metrics.recent_snapshot_for(-1001, 11)
        other_chat = metrics.recent_snapshot_for(-2002, 10)

        self.assertEqual((topic_10.attempts, topic_10.successes, topic_10.no_output), (1, 1, 0))
        self.assertEqual(topic_10.engine_counts, {"v3": 1})
        self.assertEqual(topic_10.mode_counts, {"direct_reply": 1})
        self.assertEqual((topic_11.attempts, topic_11.successes, topic_11.no_output), (1, 0, 1))
        self.assertEqual(topic_11.mode_counts, {"autonomous": 1})
        self.assertEqual((other_chat.attempts, other_chat.successes, other_chat.no_output), (1, 1, 0))
        self.assertEqual(other_chat.engine_counts, {"v2": 1})


if __name__ == "__main__":
    unittest.main()

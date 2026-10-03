from __future__ import annotations

import unittest

from entertainment.generation_metrics import GenerationMetrics, aggregate_generation_actions
from entertainment.generation_v3 import GenerationResult
from entertainment.models import EntertainmentActionRecord, EntertainmentActionType


def action(metadata: dict[str, object], *, created_at: int = 1000) -> EntertainmentActionRecord:
    return EntertainmentActionRecord(
        id=1,
        chat_id=-1001,
        topic_id=10,
        action_type=EntertainmentActionType.REMIXED_PHRASE,
        trigger_message_id=None,
        created_at=created_at,
        metadata=metadata,
    )


class GenerationMetricsTests(unittest.TestCase):
    def test_live_metrics_count_success_and_no_output_without_raw_text(self):
        metrics = GenerationMetrics()
        result = GenerationResult(
            text="SECRET GENERATED TEXT",
            engine="v3",
            score=5.2,
            candidate_count=12,
            rejection_counts={"exact_source": 3, "single_source": 2},
        )

        metrics.record(mode="direct_reply", engine="v3", result=result)
        metrics.record(mode="autonomous", engine="v3", result=None)
        snapshot = metrics.snapshot()

        self.assertEqual(snapshot.attempts, 2)
        self.assertEqual(snapshot.successes, 1)
        self.assertEqual(snapshot.no_output, 1)
        self.assertEqual(snapshot.engine_counts, {"v3": 2})
        self.assertEqual(snapshot.mode_counts, {"direct_reply": 1, "autonomous": 1})
        self.assertEqual(snapshot.score_buckets, {"high": 1})
        self.assertEqual(snapshot.rejection_counts, {"exact_source": 3, "single_source": 2})
        self.assertAlmostEqual(snapshot.average_candidate_count, 12.0)
        self.assertNotIn("SECRET", repr(snapshot))

    def test_persisted_aggregation_uses_only_safe_generation_metadata(self):
        records = [
            action(
                {
                    "generation_engine": "v3",
                    "generation_mode": "autonomous",
                    "generation_candidate_count": 9,
                    "generation_score_bucket": "high",
                    "generation_rejections": {"exact_source": 2},
                    "output": "SECRET OUTPUT",
                    "trigger_text": "SECRET TRIGGER",
                    "context_messages": ["SECRET CONTEXT"],
                }
            ),
            action(
                {
                    "generation_engine": "v2",
                    "generation_mode": "direct_reply",
                    "generation_candidate_count": 1,
                    "generation_score_bucket": "none",
                    "generation_rejections": {},
                },
                created_at=1001,
            ),
            action({"phase": "active", "output": "not a generation diagnostic"}, created_at=1002),
        ]

        summary = aggregate_generation_actions(records)

        self.assertEqual(summary.attempts, 2)
        self.assertEqual(summary.successes, 2)
        self.assertEqual(summary.no_output, 0)
        self.assertEqual(summary.engine_counts, {"v3": 1, "v2": 1})
        self.assertEqual(summary.mode_counts, {"autonomous": 1, "direct_reply": 1})
        self.assertEqual(summary.rejection_counts, {"exact_source": 2})
        self.assertAlmostEqual(summary.average_candidate_count, 5.0)
        rendered = repr(summary)
        self.assertNotIn("SECRET OUTPUT", rendered)
        self.assertNotIn("SECRET TRIGGER", rendered)
        self.assertNotIn("SECRET CONTEXT", rendered)

    def test_malformed_persisted_diagnostics_fail_closed(self):
        summary = aggregate_generation_actions(
            [
                action(
                    {
                        "generation_engine": ["v3"],
                        "generation_mode": {"bad": "shape"},
                        "generation_candidate_count": "many",
                        "generation_score_bucket": object(),
                        "generation_rejections": {"exact_source": "lots", 7: 3},
                    }
                )
            ]
        )

        self.assertEqual(summary.attempts, 0)
        self.assertEqual(summary.successes, 0)
        self.assertEqual(summary.engine_counts, {})
        self.assertEqual(summary.rejection_counts, {})
        self.assertEqual(summary.average_candidate_count, 0.0)


if __name__ == "__main__":
    unittest.main()

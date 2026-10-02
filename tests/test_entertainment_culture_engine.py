import unittest
from collections import Counter

from entertainment.culture import build_conversation_runs, build_culture_context
from entertainment.models import MemoryEvent, MemoryEventType


def event(
    message_id: int,
    text: str,
    *,
    user_id: int = 1,
    created_at: int = 1000,
    reply_to_message_id: int | None = None,
    topic_id: int = 10,
) -> MemoryEvent:
    return MemoryEvent(
        id=message_id,
        chat_id=-1001,
        topic_id=topic_id,
        message_id=message_id,
        user_id=user_id,
        event_type=MemoryEventType.TEXT,
        created_at=created_at,
        text=text,
        reply_to_message_id=reply_to_message_id,
    )


class CultureEngineTests(unittest.TestCase):
    def test_conversation_runs_split_on_eight_minute_gap(self):
        events = [
            event(1, "первый кусок", created_at=1000),
            event(2, "второй кусок", created_at=1479),
            event(3, "новый разговор", created_at=1960),
        ]

        runs = build_conversation_runs(events, gap_seconds=480)

        self.assertEqual([[item.message_id for item in run] for run in runs], [[1, 2], [3]])

    def test_same_user_split_messages_create_joined_phrase_source(self):
        recent = [
            event(1, "еду ночью на автобусе", user_id=7, created_at=1000),
            event(2, "потом сразу до вокзала", user_id=7, created_at=1060),
        ]

        context = build_culture_context(recent, [])

        self.assertIn("еду ночью на автобусе потом сразу до вокзала", context.source_messages)

    def test_cross_user_turns_influence_weight_without_raw_token_fusion(self):
        recent = [
            event(1, "поезд опять опоздал", user_id=1, created_at=1000),
            event(2, "зато чай горячий", user_id=2, created_at=1020),
        ]

        context = build_culture_context(recent, [])

        self.assertIn("поезд опять опоздал", context.source_messages)
        self.assertIn("зато чай горячий", context.source_messages)
        self.assertNotIn("поезд опять опоздал зато чай горячий", context.source_messages)
        self.assertGreater(context.source_messages.count("зато чай горячий"), 1)

    def test_explicit_reply_outweighs_accidental_adjacency(self):
        recent = [
            event(1, "автобус уже приехал", user_id=1, created_at=1000),
            event(2, "опыт будет полезный", user_id=2, created_at=1020),
            event(
                3,
                "зато потом поспим",
                user_id=3,
                created_at=1040,
                reply_to_message_id=1,
            ),
        ]

        context = build_culture_context(recent, [])
        counts = Counter(context.source_messages)

        self.assertGreater(counts["зато потом поспим"], counts["опыт будет полезный"])

    def test_missing_reply_target_falls_back_without_error(self):
        recent = [
            event(
                2,
                "ответ на уже удаленное",
                user_id=2,
                created_at=1020,
                reply_to_message_id=999,
            )
        ]

        context = build_culture_context(recent, [])

        self.assertIn("ответ на уже удаленное", context.source_messages)

    def test_recent_sources_are_weighted_above_historical_windows(self):
        recent = [event(10, "сейчас говорим про автобус", created_at=5000)]
        historical = [[event(1, "старый мем про метро", created_at=1000)]]

        context = build_culture_context(recent, historical)
        counts = Counter(context.source_messages)

        self.assertGreater(counts["сейчас говорим про автобус"], counts["старый мем про метро"])
        self.assertEqual(context.recent_event_count, 1)
        self.assertEqual(context.historical_event_count, 1)

    def test_trigger_text_is_strong_current_context(self):
        recent = [event(1, "мы обсуждали вокзал", created_at=1000)]

        context = build_culture_context(recent, [], trigger_text="а автобус когда будет")

        self.assertEqual(context.context_messages[-1], "а автобус когда будет")
        self.assertGreaterEqual(context.context_messages.count("а автобус когда будет"), 2)


if __name__ == "__main__":
    unittest.main()

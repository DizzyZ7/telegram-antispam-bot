"""Regression tests for entertainment response quality gates."""
import unittest

from entertainment.novelty import is_contextually_relevant, is_novel_generated_text


class EntertainmentQualityTests(unittest.TestCase):
    def test_recent_bot_paraphrase_is_rejected(self):
        previous = "енот сегодня принес огромный чайник и спрятал его на чердаке"
        similar = "енот сегодня принес огромный чайник и поставил его на чердаке"
        self.assertFalse(is_novel_generated_text(similar, [], [previous]))

    def test_distinct_response_is_allowed(self):
        self.assertTrue(is_novel_generated_text(
            "кот сегодня придумал новую историю о космическом путешествии",
            [],
            ["енот принес чайник на чердак и спрятал тапок"],
        ))

    def test_direct_reply_must_share_subject(self):
        self.assertFalse(is_contextually_relevant(
            "сегодня енот готовит чай на кухне",
            "Как тебе новая глава про дракона?",
        ))
        self.assertTrue(is_contextually_relevant(
            "дракона сегодня снова обсуждают всем чатом",
            "Как тебе новая глава про дракона?",
        ))

    def test_short_trigger_does_not_silence_reply(self):
        self.assertTrue(is_contextually_relevant("енот сегодня снова здесь", "ага"))


if __name__ == "__main__":
    unittest.main()

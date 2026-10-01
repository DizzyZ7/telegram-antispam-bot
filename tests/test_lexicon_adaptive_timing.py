import unittest

from minigames import MiniGameService


class LexiconAdaptiveRoundTimingTests(unittest.TestCase):
    def test_round_duration_scales_by_hundred_word_buckets(self) -> None:
        cases = (
            (0, 5 * 60),
            (38, 5 * 60),
            (99, 5 * 60),
            (100, 10 * 60),
            (101, 10 * 60),
            (199, 10 * 60),
            (200, 15 * 60),
            (270, 15 * 60),
            (299, 15 * 60),
            (300, 20 * 60),
        )

        for total_words, expected_seconds in cases:
            with self.subTest(total_words=total_words):
                self.assertEqual(
                    MiniGameService.round_duration_seconds(total_words),
                    expected_seconds,
                )

    def test_negative_word_count_falls_back_to_minimum_round(self) -> None:
        self.assertEqual(MiniGameService.round_duration_seconds(-1), 5 * 60)


if __name__ == "__main__":
    unittest.main()

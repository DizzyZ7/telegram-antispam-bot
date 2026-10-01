import unittest

from minigames import MiniGameService, WordGameRound


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

    def test_live_round_extends_when_dynamic_word_count_crosses_threshold(self) -> None:
        service = object.__new__(MiniGameService)
        round_data = WordGameRound(
            chat_id=1,
            round_code="TEST",
            base_word="тестирование",
            allowed_words=set(),
            min_length=4,
            started_at=1_000.0,
            ends_at=1_300.0,
            message_thread_id=None,
        )

        self.assertEqual(service.extend_round_deadline_for_total_words(round_data, 99), 0)
        self.assertEqual(round_data.ends_at, 1_300.0)

        self.assertEqual(service.extend_round_deadline_for_total_words(round_data, 100), 300)
        self.assertEqual(round_data.ends_at, 1_600.0)

        self.assertEqual(service.extend_round_deadline_for_total_words(round_data, 199), 0)
        self.assertEqual(round_data.ends_at, 1_600.0)

        self.assertEqual(service.extend_round_deadline_for_total_words(round_data, 200), 300)
        self.assertEqual(round_data.ends_at, 1_900.0)

    def test_live_round_deadline_never_shrinks(self) -> None:
        service = object.__new__(MiniGameService)
        round_data = WordGameRound(
            chat_id=1,
            round_code="TEST",
            base_word="тестирование",
            allowed_words=set(),
            min_length=4,
            started_at=1_000.0,
            ends_at=1_900.0,
            message_thread_id=None,
        )

        self.assertEqual(service.extend_round_deadline_for_total_words(round_data, 70), 0)
        self.assertEqual(round_data.ends_at, 1_900.0)


if __name__ == "__main__":
    unittest.main()

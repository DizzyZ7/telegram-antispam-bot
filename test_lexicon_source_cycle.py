from __future__ import annotations

import unittest

from lexicon_source_cycle import choose_cycle_word, unique_words


class LexiconSourceCycleTests(unittest.TestCase):
    def test_unique_words_keeps_order(self) -> None:
        self.assertEqual(unique_words(["альфа", "бета", "альфа", "", "гамма"]), ["альфа", "бета", "гамма"])

    def test_current_cycle_never_repeats(self) -> None:
        candidates = ["альфа", "бета", "гамма", "дельта"]
        used: set[str] = set()
        sequence: list[str] = []

        for _ in range(len(candidates)):
            word, restarted = choose_cycle_word(
                candidates,
                used,
                sequence[-1] if sequence else None,
                chooser=lambda values: values[0],
            )
            self.assertFalse(restarted)
            self.assertNotIn(word, used)
            used.add(word)
            sequence.append(word)

        self.assertEqual(sequence, candidates)

    def test_new_cycle_does_not_repeat_boundary_word(self) -> None:
        candidates = ["альфа", "бета", "гамма"]
        word, restarted = choose_cycle_word(
            candidates,
            set(candidates),
            "гамма",
            chooser=lambda values: values[0],
        )
        self.assertTrue(restarted)
        self.assertEqual(word, "альфа")
        self.assertNotEqual(word, "гамма")


if __name__ == "__main__":
    unittest.main()

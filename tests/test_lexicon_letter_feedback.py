from __future__ import annotations

import unittest

from lexicon_letter_feedback import (
    format_missing_letters,
    missing_letter_counts,
    missing_letters_label,
)


class LexiconLetterFeedbackTests(unittest.TestCase):
    def test_missing_single_letter(self) -> None:
        missing = missing_letter_counts("трек", "гиперпространство")
        self.assertEqual(missing, (("к", 1),))
        self.assertEqual(format_missing_letters(missing), "к")
        self.assertEqual(missing_letters_label(missing), "буквы")

    def test_missing_repeated_letter_quantity(self) -> None:
        missing = missing_letter_counts("касса", "коса")
        self.assertEqual(missing, (("а", 1), ("с", 1)))
        self.assertEqual(format_missing_letters(missing), "а, с")
        self.assertEqual(missing_letters_label(missing), "букв")

    def test_word_that_fits_has_no_missing_letters(self) -> None:
        self.assertEqual(missing_letter_counts("раствор", "гиперпространство"), ())

    def test_existing_examples_from_live_round(self) -> None:
        source = "гиперпространство"
        self.assertEqual(missing_letter_counts("трак", source), (("к", 1),))
        self.assertEqual(missing_letter_counts("стак", source), (("к", 1),))
        self.assertEqual(missing_letter_counts("трек", source), (("к", 1),))


if __name__ == "__main__":
    unittest.main()

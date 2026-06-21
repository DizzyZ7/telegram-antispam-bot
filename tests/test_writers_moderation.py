import unittest

from writers_moderation import (
    WRITERS_RULES_URL,
    build_captcha_success_text,
    contains_prohibited_language,
)


class ProhibitedLanguageTests(unittest.TestCase):
    def test_detects_high_confidence_forms(self):
        for text in ("блять", "пиздец", "пiздa", "blyat", "fuck"):
            with self.subTest(text=text):
                self.assertTrue(contains_prohibited_language(text))

    def test_does_not_match_normal_words(self):
        for text in ("бляха", "суккулент", "педикюр", "хулиган", "обычный разговор"):
            with self.subTest(text=text):
                self.assertFalse(contains_prohibited_language(text))

    def test_captcha_success_repeats_rules_link(self):
        text = build_captcha_success_text("@qraxos")
        self.assertIn("@qraxos", text)
        self.assertIn(WRITERS_RULES_URL, text)
        self.assertIn("Правила чата", text)


if __name__ == "__main__":
    unittest.main()

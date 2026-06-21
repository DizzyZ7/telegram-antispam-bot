import unittest

from writers_moderation import (
    RULES_LINK_PREVIEW_OPTIONS,
    WRITERS_RULES_URL,
    build_captcha_success_text,
    contains_prohibited_language,
)


class ProhibitedLanguageTests(unittest.TestCase):
    def test_detects_obscene_and_toxic_variants(self):
        for text in (
            "\u0431\u043b\u044f\u0442\u044c",
            "\u043f\u0438\u0437\u0434\u0435\u0446",
            "\u043fi\u0437d\u0430",
            "blyat",
            f"f{'u' * 3}ck",
            "\u043f\u0438\u0434\u0430\u0440\u0430\u0441\u0438\u043d\u0430",
            "\u043f\u0438\u0434\u043e\u0440\u0430\u0441\u0438\u043d\u0430",
            "pidarasina",
            "debil",
            "\u0434\u0435\u0431\u0438\u043b",
            "\u0434\u0435\u0431\u0438\u043b\u044c\u043d\u0430\u044f",
            "\u043c\u0440\u0430\u0437\u043e\u0442\u0430",
            "\u0447\u043c\u043e\u0448\u043d\u0438\u043a",
            "\u043f-\u0438-\u0434-\u043e-\u0440-\u0430-\u0441",
            "\u0434 \u0435 \u0431 \u0438 \u043b",
        ):
            with self.subTest(text=text):
                self.assertTrue(contains_prohibited_language(text))

    def test_does_not_match_normal_words(self):
        for text in (
            "\u0431\u043b\u044f\u0445\u0430",
            "\u0441\u0443\u043a\u043a\u0443\u043b\u0435\u043d\u0442",
            "\u043f\u0435\u0434\u0438\u043a\u044e\u0440",
            "\u0445\u0443\u043b\u0438\u0433\u0430\u043d",
            "\u0442\u0432\u0430\u0440\u044c \u0438\u0437 \u0431\u0435\u0441\u0442\u0438\u0430\u0440\u0438\u044f",
            "\u0418\u0434\u0438\u043e\u0442 \u0414\u043e\u0441\u0442\u043e\u0435\u0432\u0441\u043a\u043e\u0433\u043e",
            "\u043e\u0431\u044b\u0447\u043d\u044b\u0439 \u0440\u0430\u0437\u0433\u043e\u0432\u043e\u0440",
        ):
            with self.subTest(text=text):
                self.assertFalse(contains_prohibited_language(text))

    def test_captcha_success_repeats_rules_link(self):
        text = build_captcha_success_text("@qraxos")
        self.assertIn("@qraxos", text)
        self.assertIn(WRITERS_RULES_URL, text)
        self.assertIn("\u041f\u0440\u0430\u0432\u0438\u043b\u0430 \u0447\u0430\u0442\u0430", text)

    def test_rules_link_preview_is_disabled(self):
        self.assertTrue(RULES_LINK_PREVIEW_OPTIONS.is_disabled)


if __name__ == "__main__":
    unittest.main()

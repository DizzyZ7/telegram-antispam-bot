import json
import unittest
from pathlib import Path

import writers_moderation as moderation
from writers_moderation import (
    LEXICON_PATH,
    MODERATION_LEXICON,
    RULES_LINK_PREVIEW_OPTIONS,
    WRITERS_RULES_URL,
    build_captcha_success_text,
    contains_prohibited_language,
    detect_prohibited_language,
)

EVALUATION_PATH = Path(__file__).parents[1] / "data" / "writers_moderation_eval.json"


class ProhibitedLanguageTests(unittest.TestCase):
    @staticmethod
    def _load_evaluation_data() -> dict:
        return json.loads(EVALUATION_PATH.read_text(encoding="utf-8"))

    def test_versioned_lexicon_is_loaded(self):
        self.assertTrue(LEXICON_PATH.exists())
        self.assertEqual(MODERATION_LEXICON.schema_version, 1)
        self.assertGreaterEqual(MODERATION_LEXICON.rule_count, 100)
        self.assertTrue(MODERATION_LEXICON.exact_mixed)
        self.assertTrue(MODERATION_LEXICON.prefix_mixed)

    def test_evaluation_corpus(self):
        data = self._load_evaluation_data()
        for text in data["must_block"]:
            with self.subTest(kind="must_block", text=text):
                self.assertTrue(contains_prohibited_language(text))
                self.assertIsNotNone(detect_prohibited_language(text))

        for text in data["must_allow"]:
            with self.subTest(kind="must_allow", text=text):
                self.assertFalse(contains_prohibited_language(text))
                self.assertIsNone(detect_prohibited_language(text))

    def test_unicode_and_symbol_bypasses(self):
        original_extra_terms = moderation.EXTRA_BLOCKED_TOKENS
        overlay_terms = ("\u0431\u0434\u044c", "\u0445\u0439", "\u043f\u0437\u0434", "pzd", "xj")
        extra_terms = set(original_extra_terms)
        for term in overlay_terms:
            extra_terms.add(moderation._normalize_mixed_token(term))
            extra_terms.add(moderation._normalize_latin_token(term))

        moderation.EXTRA_BLOCKED_TOKENS = frozenset(extra_terms)
        try:
            blocked = (
                "\u0451\u0431\u0430\u043d\u044b\u0439",
                "\u0445u\u0439",
                "\u0445*\u0439",
                "\u0445\U0001f595\u0439",
                "\u0431**\u0434\u044c",
                "\u043f*\u0437\u0434\u0430",
                "\u043f \u0438 \u0434 \u043e \u0440",
            )
            for text in blocked:
                with self.subTest(kind="must_block", text=text):
                    self.assertTrue(contains_prohibited_language(text))
        finally:
            moderation.EXTRA_BLOCKED_TOKENS = original_extra_terms

    def test_symbol_pass_does_not_merge_normal_words(self):
        for text in (
            "\u043e\u043d \u0441\u043a\u0430\u0437\u0430\u043b: \u044d\u0442\u043e, \u0434\u0430",
            "\u0431\u043b\u044f\u0445\u0430 \u0432 \u0434\u0438\u0430\u043b\u043e\u0433\u0435",
            "\u0441\u0443\u043a\u043a\u0443\u043b\u0435\u043d\u0442 \u043d\u0430 \u043e\u043a\u043d\u0435",
        ):
            with self.subTest(kind="must_allow", text=text):
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

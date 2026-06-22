import json
import unittest
from pathlib import Path

from moderation_dataset import DATASET_PATH, load_curated_terms
from writers_moderation import (
    RULES_LINK_PREVIEW_OPTIONS,
    WRITERS_RULES_URL,
    build_captcha_success_text,
    contains_prohibited_language,
)

EVALUATION_PATH = Path(__file__).parents[1] / "data" / "writers_moderation_eval.json"


class ProhibitedLanguageTests(unittest.TestCase):
    @staticmethod
    def _load_evaluation_data() -> dict:
        return json.loads(EVALUATION_PATH.read_text(encoding="utf-8"))

    def test_curated_lexicon_is_available(self):
        terms = load_curated_terms()
        self.assertTrue(DATASET_PATH.exists())
        self.assertGreaterEqual(len(terms), 40)
        self.assertEqual(len(terms), len(set(term.casefold() for term in terms)))

    def test_evaluation_corpus(self):
        data = self._load_evaluation_data()
        for text in data["must_block"]:
            with self.subTest(kind="must_block", text=text):
                self.assertTrue(contains_prohibited_language(text))

        for text in data["must_allow"]:
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

from __future__ import annotations

import unittest

from entertainment.language import analyze_token, build_topic_anchor


class GenerationV3LanguageTests(unittest.TestCase):
    def test_russian_word_has_lemma_and_pos(self):
        token = analyze_token("Машины")
        self.assertEqual(token.surface, "Машины")
        self.assertEqual(token.normalized, "машины")
        self.assertEqual(token.lemma, "машина")
        self.assertEqual(token.pos, "NOUN")

    def test_unknown_chat_token_falls_back_without_rejection(self):
        token = analyze_token("DizZy_Z7")
        self.assertEqual(token.normalized, "dizzy_z7")
        self.assertEqual(token.lemma, "dizzy_z7")
        self.assertIsNone(token.pos)
        self.assertFalse(token.morph_confident)

    def test_analysis_cache_is_bounded_to_4096_forms(self):
        info = analyze_token.cache_info()
        self.assertEqual(info.maxsize, 4096)

    def test_topic_anchor_uses_only_latest_40_context_messages(self):
        context = ["древнийархив отдельная тема"] * 10
        context.extend(["ну да"] * 45)
        context.extend([
            "автобус едет к вокзалу",
            "ждем автобус на остановке",
        ])
        anchor = build_topic_anchor(context, trigger_text=None, direct_reply=False)

        self.assertIn("автобус", anchor.lemma_weights)
        self.assertIn("вокзал", anchor.lemma_weights)
        self.assertNotIn("древнийархив", anchor.lemma_weights)
        self.assertGreater(anchor.relevance(["автобусы", "вокзал"]), 0.0)

    def test_direct_trigger_is_weighted_once_even_when_legacy_context_duplicates_it(self):
        trigger = "где наш автобус"
        context = [
            "разговор про метро",
            trigger,
            trigger,
        ]
        anchor = build_topic_anchor(context, trigger_text=trigger, direct_reply=True)

        self.assertAlmostEqual(anchor.lemma_weights["автобус"], 4.0)
        self.assertGreater(anchor.lemma_weights["автобус"], anchor.lemma_weights["метро"])
        self.assertEqual(anchor.trigger_terms, frozenset({"автобус"}))

    def test_commands_and_slang_remain_surface_anchors(self):
        anchor = build_topic_anchor(
            ["/spawn mew pls", "заспавни кота пж"],
            trigger_text=None,
            direct_reply=False,
        )
        self.assertIn("spawn", anchor.surface_weights)
        self.assertGreater(anchor.relevance(["/spawn", "mew"]), 0.0)


if __name__ == "__main__":
    unittest.main()

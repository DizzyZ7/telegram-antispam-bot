from __future__ import annotations

import random
import unittest

from entertainment.generation_v3 import (
    GenerationMode,
    GenerationRequest,
    GenerationV3,
    build_transition_model,
    distinct_source_identities,
    has_repeated_ngram,
    longest_contiguous_word_overlap,
    normalize_source_text,
)


class GenerationV3CoreTests(unittest.TestCase):
    def test_transition_model_backs_off_from_five_tokens_to_one(self):
        model = build_transition_model(
            [
                "альфа бета гамма дельта эпсилон один",
                "омега бета гамма дельта эпсилон два",
            ]
        )

        order, options = model.next_options(
            ["альфа", "бета", "гамма", "дельта", "эпсилон"]
        )
        self.assertEqual(order, 5)
        self.assertEqual(set(options), {"один"})

        order, options = model.next_options(
            ["икс", "бета", "гамма", "дельта", "эпсилон"]
        )
        self.assertEqual(order, 4)
        self.assertEqual(set(options), {"один", "два"})

        order, options = model.next_options(["икс", "эпсилон"])
        self.assertEqual(order, 1)
        self.assertEqual(set(options), {"один", "два"})

    def test_normalized_source_identity_ignores_case_space_and_terminal_punctuation(self):
        first = normalize_source_text("  Автобус   уже едет к вокзалу!!! ")
        second = normalize_source_text("автобус уже едет к вокзалу.")
        self.assertEqual(first, second)
        self.assertEqual(
            distinct_source_identities(
                [
                    "Автобус уже едет к вокзалу!",
                    " автобус уже едет к вокзалу. ",
                    "АВТОБУС УЖЕ ЕДЕТ К ВОКЗАЛУ",
                ]
            ),
            {first},
        )

    def test_longest_contiguous_overlap_is_measured_in_words(self):
        overlap = longest_contiguous_word_overlap(
            "автобус уже едет к вокзалу",
            "наш автобус уже едет к вокзалу ночью",
        )
        self.assertEqual(overlap, 5)

    def test_repeated_trigram_loop_is_detected(self):
        self.assertTrue(has_repeated_ngram("кот идет домой кот идет домой", size=3))
        self.assertFalse(has_repeated_ngram("кот идет домой а пес остается тут", size=3))

    def test_weighted_duplicates_do_not_fake_multi_source_composition(self):
        source = "автобус сегодня едет через северный вокзал вечером без остановок"
        request = GenerationRequest(
            source_messages=[source] * 12,
            context_messages=["ждем автобус у вокзала"],
            trigger_text=None,
            mode=GenerationMode.AUTONOMOUS,
            recent_bot_outputs=[],
        )

        result = GenerationV3().generate(request, rng=random.Random(7))
        self.assertIsNone(result)

    def test_generation_is_deterministic_and_composes_distinct_sources(self):
        sources = [
            "автобус скоро приедет к вокзалу и заберет всех домой",
            "метро уже закрывается на ночь и город становится тихим",
            "автобус вечером идет через центр и люди ждут его у вокзала",
            "трамвай сворачивает после моста и город постепенно засыпает",
            "мы сегодня ждем автобус и потом сразу едем домой",
            "у вокзала шумно вечером и последние автобусы еще ходят",
        ]
        request = GenerationRequest(
            source_messages=sources,
            context_messages=[
                "когда автобус будет у вокзала",
                "автобус уже скоро должен приехать",
            ],
            trigger_text=None,
            mode=GenerationMode.AUTONOMOUS,
            recent_bot_outputs=[],
        )

        first = GenerationV3().generate(request, rng=random.Random(19))
        second = GenerationV3().generate(request, rng=random.Random(19))

        self.assertIsNotNone(first)
        self.assertEqual(first, second)
        self.assertEqual(first.engine, "v3")
        self.assertGreater(first.candidate_count, 0)
        self.assertNotIn(
            normalize_source_text(first.text),
            distinct_source_identities(sources),
        )
        self.assertIn("автобус", normalize_source_text(first.text))

    def test_direct_reply_keeps_trigger_as_strong_topic_anchor(self):
        sources = [
            "автобус задержался у вокзала и водитель ждет пассажиров",
            "электричка ушла на север и платформа снова опустела",
            "автобус приехал вечером и люди быстро поехали домой",
            "метро закрылось рано и город стал заметно тише",
            "у вокзала автобус стоит и двери пока еще открыты",
            "трамвай свернул к мосту и улица снова стала пустой",
        ]
        request = GenerationRequest(
            source_messages=sources,
            context_messages=["обсуждаем транспорт", "где наш автобус"],
            trigger_text="где наш автобус",
            mode=GenerationMode.DIRECT_REPLY,
            recent_bot_outputs=[],
        )

        result = GenerationV3().generate(request, rng=random.Random(31))
        self.assertIsNotNone(result)
        self.assertIn("автобус", normalize_source_text(result.text))

    def test_direct_reply_reranker_prefers_candidate_covering_all_supported_trigger_terms(self):
        sources = [
            "автобус скоро приедет к вокзалу и заберет всех домой",
            "метро уже закрывается на ночь и город становится тихим",
            "автобус вечером идет через центр и люди ждут его у вокзала",
            "трамвай сворачивает после моста и город постепенно засыпает",
            "мы сегодня ждем автобус и потом сразу едем домой",
            "у вокзала шумно вечером и последние автобусы еще ходят",
        ]
        request = GenerationRequest(
            source_messages=sources * 5,
            context_messages=[
                "когда автобус будет у вокзала",
                "автобус уже скоро должен приехать",
            ],
            trigger_text="где наш автобус у вокзала",
            mode=GenerationMode.DIRECT_REPLY,
            recent_bot_outputs=["вчера уже шутили про метро"],
        )

        result = GenerationV3().generate(request, rng=random.Random(17))
        self.assertIsNotNone(result)
        normalized = normalize_source_text(result.text)
        self.assertIn("автобус", normalized)
        self.assertIn("вокзал", normalized)


if __name__ == "__main__":
    unittest.main()

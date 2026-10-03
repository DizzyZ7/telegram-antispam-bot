from __future__ import annotations

import random
import unittest
from unittest.mock import patch

import entertainment.generation_v3 as generation_v3
from entertainment.generation_v3 import (
    GenerationMode,
    GenerationRequest,
    GenerationV3,
    TransitionModel,
    longest_contiguous_word_overlap,
    normalize_source_text,
)


class GenerationV3RuntimeHardeningTests(unittest.TestCase):
    @staticmethod
    def _large_request() -> GenerationRequest:
        messages = [
            f"архивная тема номер {index} про старый разговор и общий мост сегодня"
            for index in range(220)
        ]
        # Keep a dense current-topic cluster near the end so a bounded working
        # set can retain topical material instead of merely taking a prefix.
        messages.extend(
            f"focus deploy номер {index} обсуждаем релиз и общий мост сегодня"
            for index in range(40)
        )
        return GenerationRequest(
            source_messages=messages,
            context_messages=[
                "сейчас обсуждаем focus deploy",
                "релиз focus deploy сегодня",
            ],
            trigger_text="focus deploy",
            mode=GenerationMode.DIRECT_REPLY,
            recent_bot_outputs=[],
        )

    def test_large_snapshot_builds_transition_model_from_bounded_working_set(self):
        request = self._large_request()
        captured_sizes: list[int] = []

        def capture_model(messages):
            materialized = list(messages)
            captured_sizes.append(len(materialized))
            return TransitionModel(indexes={})

        engine = GenerationV3(candidate_target=32)
        with (
            patch.object(generation_v3, "build_transition_model", side_effect=capture_model),
            patch.object(GenerationV3, "_structured_crossover_candidates", return_value=[]),
            patch.object(GenerationV3, "_crossover_candidates", return_value=[]),
            patch.object(GenerationV3, "_backoff_candidates", return_value=[]),
        ):
            result = engine.generate(request, rng=random.Random(1))

        self.assertIsNone(result)
        self.assertEqual(len(captured_sizes), 1)
        self.assertLessEqual(captured_sizes[0], 128)

    def test_source_outside_working_set_still_cannot_be_replayed(self):
        hidden_source = "совсем старая уникальная фраза которую нельзя повторять дословно сейчас"
        topical_sources = [
            f"focus deploy релиз номер {index} обсуждаем общий мост прямо сегодня"
            for index in range(180)
        ]
        request = GenerationRequest(
            source_messages=[hidden_source, *topical_sources],
            context_messages=["focus deploy релиз сегодня"],
            trigger_text="focus deploy",
            mode=GenerationMode.DIRECT_REPLY,
            recent_bot_outputs=[],
        )
        engine = GenerationV3(candidate_target=32)

        with (
            patch.object(
                GenerationV3,
                "_structured_crossover_candidates",
                return_value=[hidden_source],
            ),
            patch.object(GenerationV3, "_crossover_candidates", return_value=[]),
            patch.object(GenerationV3, "_backoff_candidates", return_value=[]),
        ):
            result = engine.generate(request, rng=random.Random(2))

        self.assertIsNone(result)

    def test_mixed_ru_en_commands_usernames_and_slang_keep_trigger_signal(self):
        messages = [
            "/spawn кота после deploy и потом идем в рейд gg",
            "@DizZyZ7 сказал deploy готов и кот уже в рейде gg",
            "когда deploy готов запускаем /spawn и ловим кота в рейде",
            "gg народ кот после spawn снова залетел в рейд",
            "deploy сегодня живой и @DizZyZ7 уже ждет spawn кота",
            "в рейде после deploy кот устроил полный gg и мем",
            "spawn кота сегодня и deploy пройдет без душниловки gg",
            "@DizZyZ7 в рейде сказал gg когда кот пришел после deploy",
        ]
        request = GenerationRequest(
            source_messages=messages,
            context_messages=["как там deploy", "когда /spawn кота @DizZyZ7"],
            trigger_text="/spawn кота @DizZyZ7",
            mode=GenerationMode.DIRECT_REPLY,
            recent_bot_outputs=[],
        )
        trigger_terms = {"spawn", "кота", "dizzyz7"}
        outputs: list[str] = []

        for seed in range(8):
            result = GenerationV3(candidate_target=32).generate(
                request,
                rng=random.Random(seed),
            )
            if result is None:
                continue
            outputs.append(result.text)
            normalized = normalize_source_text(result.text)
            words = set(normalized.split())
            self.assertTrue(words & trigger_terms)
            self.assertLessEqual(len(normalized.split()), 24)
            self.assertNotIn(normalized, {normalize_source_text(item) for item in messages})
            self.assertLessEqual(
                max(longest_contiguous_word_overlap(result.text, source) for source in messages),
                6,
            )

        self.assertGreaterEqual(len(outputs), 6)

    def test_candidate_target_remains_hard_clamped(self):
        self.assertEqual(GenerationV3(candidate_target=1).candidate_target, 32)
        self.assertEqual(GenerationV3(candidate_target=10_000).candidate_target, 64)


if __name__ == "__main__":
    unittest.main()

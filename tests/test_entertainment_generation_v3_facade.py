from __future__ import annotations

import os
import random
import unittest
from unittest.mock import patch

from entertainment.config import resolve_generation_engine
from entertainment.generation import generate_chat_text, generate_text
from entertainment.generation_v3 import GenerationMode, GenerationRequest
from tests.test_entertainment_generation_v2 import TRAVEL_MESSAGES


V3_SOURCES = [
    "автобус скоро приедет к вокзалу и заберет всех домой",
    "метро уже закрывается на ночь и город становится тихим",
    "автобус вечером идет через центр и люди ждут его у вокзала",
    "трамвай сворачивает после моста и город постепенно засыпает",
    "мы сегодня ждем автобус и потом сразу едем домой",
    "у вокзала шумно вечером и последние автобусы еще ходят",
]


def _request(messages: list[str]) -> GenerationRequest:
    return GenerationRequest(
        source_messages=messages,
        context_messages=["ждем автобус у вокзала", "автобус скоро будет"],
        trigger_text=None,
        mode=GenerationMode.AUTONOMOUS,
        recent_bot_outputs=[],
    )


class GenerationV3FacadeTests(unittest.TestCase):
    def test_selector_defaults_to_v3(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ENTERTAINMENT_GENERATION_ENGINE", None)
            self.assertEqual(resolve_generation_engine(), "v3")

    def test_selector_accepts_v2_and_invalid_value_falls_back_to_v3(self):
        self.assertEqual(resolve_generation_engine("V2"), "v2")
        self.assertEqual(resolve_generation_engine(" v3 "), "v3")
        self.assertEqual(resolve_generation_engine("mystery"), "v3")

    def test_generate_text_uses_v3_by_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ENTERTAINMENT_GENERATION_ENGINE", None)
            result = generate_text(_request(V3_SOURCES), rng=random.Random(19))
        self.assertIsNotNone(result)
        self.assertEqual(result.engine, "v3")

    def test_env_can_roll_back_to_v2_without_changing_callers(self):
        corpus = TRAVEL_MESSAGES * 3
        request = _request(corpus)
        with patch.dict(os.environ, {"ENTERTAINMENT_GENERATION_ENGINE": "v2"}):
            result = generate_text(request, rng=random.Random(3))
        self.assertIsNotNone(result)
        self.assertEqual(result.engine, "v2")

    def test_explicit_engine_overrides_environment(self):
        with patch.dict(os.environ, {"ENTERTAINMENT_GENERATION_ENGINE": "v2"}):
            result = generate_text(
                _request(V3_SOURCES),
                rng=random.Random(19),
                engine="v3",
            )
        self.assertIsNotNone(result)
        self.assertEqual(result.engine, "v3")

    def test_existing_v2_function_remains_compatible(self):
        generated = generate_chat_text(
            TRAVEL_MESSAGES * 3,
            rng=random.Random(3),
            context_messages=TRAVEL_MESSAGES[-8:],
        )
        self.assertIsNotNone(generated)


if __name__ == "__main__":
    unittest.main()

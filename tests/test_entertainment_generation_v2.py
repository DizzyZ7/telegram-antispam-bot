from __future__ import annotations

import random
import re
import unittest

from entertainment.generation import generate_chat_text, tokenize


TRAVEL_MESSAGES = [
    "В сон уже клонит, а на автобусе опыт будет.",
    "В сон уже клонит, я ни разу на автобусах так просто не ездил, опыт будет.",
    "Наметил много куда, а это Площадь Трех вокзалов.",
    "Капец, но проблема в том, что в сон уже клонит.",
    "А там лифты не туда, с другом все решили.",
    "Всем спокойной ночи, пусть работает.",
    "В субботу встречусь с друзьями, поехали туда.",
    "Вот размышляю по поводу своих поездок.",
    "Электричка скорее подойдет до вокзала.",
    "О, привет! Как добрались до вокзала?",
    "Автобус ночью идет дольше, зато можно посмотреть город.",
    "На вокзале сначала искал нужную платформу, потом разобрался.",
    "До площади можно доехать на электричке или автобусе.",
    "После поездки будет что вспомнить, даже если сейчас хочется спать.",
    "С другом договорились встретиться у вокзала и дальше ехать вместе.",
]

OTHER_MESSAGES = [
    "Сегодня в игре наконец выпал редкий предмет.",
    "Команда собралась вечером и пошла в новый рейд.",
    "Босс оказался сильнее, чем мы ожидали вчера.",
    "После победы всем выдали награды и опыт.",
    "Новый персонаж выглядит смешно, но играет отлично.",
    "На сервере вечером было много игроков.",
    "Мы долго выбирали карту для следующего матча.",
    "Урон получился выше после замены экипировки.",
    "В следующем сезоне обещают новые задания.",
    "Рейтинг поднялся после нескольких хороших матчей.",
    "Вчера тестировали новую механику вместе с командой.",
    "Редкий предмет пришлось искать почти весь вечер.",
    "В рейде лучше заранее распределить роли игроков.",
    "После обновления интерфейс игры стал намного удобнее.",
    "Следующий матч решили сыграть уже завтра вечером.",
]


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().casefold().rstrip(".!?…"))


def _words(text: str) -> list[str]:
    return [token.casefold() for token in tokenize(text) if re.search(r"[A-Za-zА-Яа-яЁё]", token)]


def _supported_trigram_ratio(text: str, messages: list[str]) -> float:
    source_trigrams: set[tuple[str, str, str]] = set()
    for message in messages:
        words = _words(message)
        source_trigrams.update(zip(words, words[1:], words[2:]))
    words = _words(text)
    generated = list(zip(words, words[1:], words[2:]))
    if not generated:
        return 0.0
    return sum(item in source_trigrams for item in generated) / len(generated)


class PhraseGenerationV2Tests(unittest.TestCase):
    def test_recent_context_biases_generation_to_current_topic(self) -> None:
        # Old history is deliberately unrelated; the latest messages are travel-related.
        corpus = OTHER_MESSAGES * 4 + TRAVEL_MESSAGES * 3
        context = TRAVEL_MESSAGES[-8:]
        travel_terms = {
            "вокзал", "вокзала", "вокзале", "автобус", "автобусе", "автобусах",
            "электричка", "электричке", "поездки", "поездку", "ехать", "доехать",
        }

        outputs: list[str] = []
        for seed in range(12):
            generated = generate_chat_text(
                corpus,
                rng=random.Random(seed),
                context_messages=context,
                candidate_count=32,
            )
            self.assertIsNotNone(generated)
            outputs.append(generated or "")

        context_hits = sum(bool(set(_words(output)) & travel_terms) for output in outputs)
        self.assertGreaterEqual(context_hits, 9, outputs)

    def test_real_chat_regressions_do_not_produce_fragmentary_junk(self) -> None:
        corpus = TRAVEL_MESSAGES * 3
        originals = {_normalized(item) for item in corpus}
        outputs: list[str] = []

        for seed in range(20):
            generated = generate_chat_text(corpus, rng=random.Random(seed), candidate_count=40)
            self.assertIsNotNone(generated)
            assert generated is not None
            outputs.append(generated)

            words = _words(generated)
            self.assertGreaterEqual(len(words), 5, generated)
            self.assertNotIn(_normalized(generated), originals, generated)
            self.assertNotRegex(generated, r":\s*\d+\s*[.!?…]?$", generated)
            self.assertNotIn("электричка скорее о привет", _normalized(generated), generated)
            self.assertNotRegex(generated, r"^[Оо],?\s+\S+[.!?…]?$", generated)

        self.assertGreaterEqual(len(set(outputs)), 8)

    def test_generated_phrase_keeps_supported_multiword_connections(self) -> None:
        corpus = TRAVEL_MESSAGES * 4
        ratios: list[float] = []
        for seed in range(16):
            generated = generate_chat_text(corpus, rng=random.Random(seed), candidate_count=40)
            self.assertIsNotNone(generated)
            assert generated is not None
            ratios.append(_supported_trigram_ratio(generated, corpus))

        self.assertGreaterEqual(sum(ratios) / len(ratios), 0.62, ratios)
        self.assertGreaterEqual(min(ratios), 0.35, ratios)


if __name__ == "__main__":
    unittest.main()

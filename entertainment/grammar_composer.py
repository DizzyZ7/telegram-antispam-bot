"""A small, bounded Russian grammar composer fed by topic vocabulary.

This is intentionally not a language model: inflection is restricted to
unambiguous noun forms, with conservative fallbacks for unknown chat words.
"""

from __future__ import annotations

import random
import re
from collections import Counter

from .moderation_safety import is_safe_entertainment_reply
from .novelty import is_novel_generated_text

_WORD = re.compile(r"[А-Яа-яЁё]{3,24}")
_STOP = frozenset({
    "это", "как", "что", "все", "всем", "мне", "тебе", "тебя",
    "они", "она", "оно", "его", "еще", "уже", "тут", "там",
    "был", "была", "были", "будет", "буду", "есть", "нет",
    "когда", "потом", "просто", "очень", "только", "сегодня",
    "завтра", "вчера", "мем", "мемы", "мемов", "доброе",
    "утро", "спокойной", "ночи", "привет", "пока", "точно",
    "почему", "какие", "какой", "какая", "которые", "который",
    "человек", "ребенок", "ребят", "работник", "сотрудник",
    "чтобы", "будто", "можно", "нужно", "снова", "никто",
    "ничего", "пишет", "писать", "сказать", "говорит",
})
# Base forms only. Avoid trying to inflect arbitrary unknown words.
_SEEDS = ("енот", "кот", "кабачок", "пельмень", "холодильник", "будильник", "робот")
_ACTIONS = (
    ("объявил", "внеплановое собрание"),
    ("открыл", "секретный департамент"),
    ("организовал", "научную конференцию"),
    ("запустил", "эксперимент без инструкции"),
    ("устроил", "внеочередной экзамен"),
    ("потребовал", "официальный выходной"),
    ("возглавил", "комиссию по странным вопросам"),
)
_ENDINGS = (
    "Комиссия пока не определилась с последствиями.",
    "Протокол заседания подписала табуретка.",
    "Независимые эксперты попросили чай.",
    "На всякий случай вызвали бухгалтера.",
    "Подробности засекретили до обеда.",
    "Свидетелем выступил подозрительно спокойный огурец.",
)
_OPENERS = ("Срочные новости:", "По последним данным,", "Тем временем", "Выяснилось:")


def _vocabulary(messages: list[str]) -> list[tuple[str, str]]:
    """Extract noun lemmas, rejecting verbs, inflected forms and unsafe words."""
    from functools import lru_cache

    import pymorphy3

    @lru_cache(maxsize=1)
    def analyzer() -> pymorphy3.MorphAnalyzer:
        return pymorphy3.MorphAnalyzer()

    counts: Counter[tuple[str, str]] = Counter()
    for message in messages:
        if "http://" in message or "https://" in message or "@" in message:
            continue
        for word in set(_WORD.findall(message.casefold().replace("ё", "е"))):
            if word in _STOP or not is_safe_entertainment_reply(word):
                continue
            parsed = analyzer().parse(word)[0]
            if parsed.tag.POS != "NOUN" or parsed.tag.number == "plur":
                continue
            if parsed.tag.animacy != "inan" or parsed.tag.gender not in {"masc", "femn", "neut"}:
                continue
            nominative = parsed.inflect({"nomn", "sing"})
            if nominative is None:
                continue
            lemma = nominative.word
            if len(lemma) < 3 or lemma in _STOP or not is_safe_entertainment_reply(lemma):
                continue
            counts[(lemma, str(parsed.tag.gender))] += 1
    return [entry for entry, _ in counts.most_common(64)]


def _agree_past(verb: str, gender: str) -> str:
    if gender == "femn":
        return verb + "а"
    if gender == "neut":
        return verb + "о"
    return verb


def generate_grammar_message(
    source_messages: list[str],
    recent_outputs: list[str],
    rng: random.Random,
) -> str | None:
    """Compose a new complete sentence; reject unsafe/replayed output."""
    learned = _vocabulary(source_messages)
    subjects = list(dict.fromkeys([*learned[:24], *((seed, "masc") for seed in _SEEDS)]))
    if not subjects:
        return None
    for _ in range(32):
        subject, gender = rng.choice(subjects)
        verb, object_phrase = rng.choice(_ACTIONS)\n        verb = _agree_past(verb, gender)
        opener = rng.choice(_OPENERS)
        ending = rng.choice(_ENDINGS)
        if opener == "Тем временем":
            candidate = f"{opener} {subject} {verb} {object_phrase}. {ending}"
        elif opener == "По последним данным,":
            candidate = f"{opener} {subject} {verb} {object_phrase}. {ending}"
        else:
            candidate = f"{opener} {subject.capitalize()} {verb} {object_phrase}. {ending}"
        if (is_safe_entertainment_reply(candidate)
            and is_novel_generated_text(candidate, source_messages, recent_outputs)):
            return candidate
    return None

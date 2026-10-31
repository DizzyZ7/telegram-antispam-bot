"""A small, bounded Russian grammar composer fed by topic vocabulary.

This is intentionally not a language model: inflection is restricted to
unambiguous noun forms, with conservative fallbacks for unknown chat words.
"""

from __future__ import annotations

import random
import re
from collections import Counter
from functools import lru_cache

import pymorphy3

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


@lru_cache(maxsize=1)
def _analyzer() -> pymorphy3.MorphAnalyzer:
    return pymorphy3.MorphAnalyzer()


def _vocabulary(messages: list[str]) -> list[tuple[str, str]]:
    """Extract inanimate noun lemmas, rejecting verbs and unsafe words."""

    counts: Counter[tuple[str, str]] = Counter()
    for message in messages:
        if "http://" in message or "https://" in message or "@" in message:
            continue
        for word in set(_WORD.findall(message.casefold().replace("ё", "е"))):
            if word in _STOP or not is_safe_entertainment_reply(word):
                continue
            parsed = _analyzer().parse(word)[0]
            if parsed.tag.POS != "NOUN":
                continue
            nominative = parsed.inflect({"nomn", "sing"})
            if nominative is None:
                continue
            lemma = nominative.word
            lemma_tag = _analyzer().parse(lemma)[0].tag
            if lemma_tag.animacy != "inan" or lemma_tag.gender not in {"masc", "femn", "neut"}:
                continue
            if len(lemma) < 3 or lemma in _STOP or not is_safe_entertainment_reply(lemma):
                continue
            counts[(lemma, str(lemma_tag.gender))] += 1
    return [entry for entry, _ in counts.most_common(64)]


def _adjectives(messages: list[str]) -> list[str]:
    """Collect adjective lemmas; inflect only through pymorphy3."""
    counts: Counter[str] = Counter()
    for message in messages:
        if "http://" in message or "https://" in message or "@" in message:
            continue
        for word in set(_WORD.findall(message.casefold().replace("ё", "е"))):
            if word in _STOP or not is_safe_entertainment_reply(word):
                continue
            parsed = _analyzer().parse(word)[0]
            if parsed.tag.POS != "ADJF":
                continue
            lemma = parsed.normal_form
            if len(lemma) >= 4 and is_safe_entertainment_reply(lemma):
                counts[lemma] += 1
    return [word for word, _ in counts.most_common(48)]


def _agree_adjective(adjective: str, gender: str) -> str | None:
    parsed = _analyzer().parse(adjective)[0]
    if parsed.tag.POS != "ADJF":
        return None
    inflected = parsed.inflect({"nomn", "sing", gender})
    return inflected.word if inflected else None


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
    adjectives = _adjectives(source_messages)
    subjects = list(dict.fromkeys([*learned[:24], *((seed, "masc") for seed in _SEEDS)]))
    if not subjects:
        return None
    for _ in range(32):
        subject, gender = rng.choice(subjects)
        modifier = None
        if adjectives and rng.random() < 0.7:
            modifier = _agree_adjective(rng.choice(adjectives), gender)
        described_subject = f"{modifier} {subject}" if modifier else subject
        verb, object_phrase = rng.choice(_ACTIONS)
        verb = _agree_past(verb, gender)
        opener = rng.choice(_OPENERS)
        ending = rng.choice(_ENDINGS)
        if opener == "Тем временем":
            candidate = f"{opener} {described_subject} {verb} {object_phrase}. {ending}"
        elif opener == "По последним данным,":
            candidate = f"{opener} {subject} {verb} {object_phrase}. {ending}"
        else:
            candidate = f"{opener} {described_subject.capitalize()} {verb} {object_phrase}. {ending}"
        if (is_safe_entertainment_reply(candidate)
            and is_novel_generated_text(candidate, source_messages, recent_outputs)):
            return candidate
    return None

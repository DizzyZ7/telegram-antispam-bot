"""Fosgen Personality v1: opt-in, topic-grounded literary interjections.

No external AI, participant profiling, names, learned personal attributes or
cross-chat memory. A selected style is ONLY a writing tone requested by a user.
Generated lines are original fixed micro-scenes, never copied user messages.
"""
from __future__ import annotations

import random
import re
from collections.abc import Iterable

PERSONALITY_STYLES = frozenset({"gentle", "ironic", "absurd"})
PERSONALITY_STYLE_LABELS = {
    "gentle": "мягкий",
    "ironic": "ирония",
    "absurd": "абсурд",
}
_STYLE_ALIASES = {
    "мягкий": "gentle", "добрый": "gentle",
    "ирония": "ironic", "ироничный": "ironic",
    "абсурд": "absurd", "хаос": "absurd",
}

# High-confidence literary triggers only. No guesses about author's private
# life, mood, age, identity or reading/writing history.
_TOPIC_PATTERNS = {
    "writing": re.compile(r"\b(?:глав[аыуеойы]|главу|главе|черновик\w*|писа(?:ть|ла|ли|тель\w*)|написа\w*|дедлайн\w*|вдохновен\w*)\b", re.I),
    "reading": re.compile(r"\b(?:чита\w*|читател\w*|прочита\w*|фикбук\w*|продолжени\w*|книг\w*)\b", re.I),
    "characters": re.compile(r"\b(?:геро[йяиюемв]|персонаж\w*|злоде\w*|сюжет\w*|пейринг\w*|финал\w*)\b", re.I),
    "covers": re.compile(r"\b(?:обложк\w*|художник\w*|палитр\w*|шрифт\w*|цвет\w*|иллюстраци\w*)\b", re.I),
}

# Topic -> style -> independently written responses. A single direct reply
# never quotes, references, or attributes anything to an actual participant.
_LINES = {
    "writing": {
        "gentle": (
            "Пусть черновик сегодня будет неровным. У прекрасных историй тоже есть право на первый дубль. ✨",
            "Совет творческого отдела: сначала разрешаем себе написать, потом — сделать красиво. 📖",
            "Маленькая сцена сегодня лучше идеальной главы, существующей только в планах. ☕",
        ),
        "ironic": (
            "Отдел дедлайнов сообщает: вдохновение снова явилось без пропуска и ушло через запасной выход.",
            "Черновик подал заявление о независимости. Автору предлагается переговорить с ним за чаем.",
            "Комиссия установила: слово «почти» в выражении «глава почти готова» юридически не ограничено.",
        ),
        "absurd": (
            "Инспекция черновиков обнаружила портал в следующую главу. На входе требуют три метафоры.",
            "Литературный принтер объявил забастовку: требует отпуск для запятых и премию для тире.",
            "Пока автор ищет вдохновение, вдохновение пытается оформить автора в библиотеку.",
        ),
    },
    "reading": {
        "gentle": (
            "Некоторые истории хочется растянуть еще на вечер. Особенно когда впереди неизвестная глава. 📚",
            "Уютная читательская магия: «Еще страницу» превращается в целый вечер.",
            "Пусть следующая глава окажется именно той, ради которой хотелось вернуться. ✨",
        ),
        "ironic": (
            "Эксперимент «не обновлять страницу с продолжением» снова завершился досрочно.",
            "Читательский совет признал: ждать продолжение труднее, чем читать предупреждения к главе.",
            "Обновление истории пока не обнаружено. Наблюдатели продолжают драматично смотреть на экран.",
        ),
        "absurd": (
            "Комитет читательского ожидания постановил построить маяк из закладок.",
            "В библиотеке обнаружен временной разлом: пять минут чтения равны двум часам.",
            "Система оповещения о новой главе включила фанфары и случайно разбудила книжного дракона.",
        ),
    },
    "characters": {
        "gentle": (
            "Хороший персонаж иногда удивляет даже того, кто придумал ему судьбу. 🌙",
            "Если герои спорят с планом, возможно, у истории просто появился собственный голос.",
            "Самые интересные повороты обычно начинаются с маленького «а что, если».",
        ),
        "ironic": (
            "Персонажи вызваны на планерку. Сюжет по-прежнему отказывается предъявлять документы.",
            "Отдел логики сюжета временно закрыт: главный герой опять пошел не туда.",
            "Злодей просит внести в контракт пункт о человеческом отношении к его мотивации.",
        ),
        "absurd": (
            "Сюжет вызвал полицию нравов: один персонаж самовольно украл чужую сюжетную арку.",
            "Главный герой открыл профсоюз второстепенных персонажей и назначил себя секретарем.",
            "Кульминация сбежала из третьего акта. Последний раз ее видели с чашкой чая.",
        ),
    },
    "covers": {
        "gentle": (
            "Иногда один точный оттенок рассказывает о книге больше целой аннотации. 🎨",
            "Обложка начинается с настроения. Остальное художник обязательно приручит.",
            "Палитра тоже умеет рассказывать истории — только без единого слова.",
        ),
        "ironic": (
            "Комиссия по обложкам требует, чтобы шрифт и фон наконец договорились о мирном сосуществовании.",
            "Цветовая палитра заявила: она не обязана соответствовать ожиданиям всех персонажей сразу.",
            "Худсовет постановил: еще один оттенок — и палитра получит собственную сюжетную линию.",
        ),
        "absurd": (
            "На обложке обнаружен заговор оттенков. Фиолетовый назначен главным подозреваемым.",
            "Шрифт открыл портал в типографическое измерение. Возвращаться отказывается.",
            "Художник вызвал дипломатическую миссию для переговоров между цветами.",
        ),
    },
}


def resolve_personality_style(raw: str) -> str | None:
    """Map explicit opt-in input to a style, never infer it from messages."""
    key = str(raw or "").strip().casefold()
    value = _STYLE_ALIASES.get(key, key)
    return value if value in PERSONALITY_STYLES else None


def _identity(text: str) -> str:
    return " ".join(re.findall(r"[a-zа-яё0-9]+", text.casefold()))


def detect_literary_topic(text: str | None) -> str | None:
    if not isinstance(text, str) or len(text) > 600 or "http" in text.casefold():
        return None
    for topic, pattern in _TOPIC_PATTERNS.items():
        if pattern.search(text):
            return topic
    return None


def choose_personality_line(
    *,
    style: str,
    trigger_text: str | None,
    recent_outputs: Iterable[str],
    rng: random.Random,
    engagement_probability: float = 0.28,
) -> tuple[str, str] | None:
    """Optional direct-reply bonus, not a replacement for Culture Memory.

    Bounded style-specific catalog, exact-topic match, one roll per attempt.
    Existing bot output is never copied or decorated with user identity.
    """
    if style not in PERSONALITY_STYLES:
        return None
    topic = detect_literary_topic(trigger_text)
    if topic is None or rng.random() >= engagement_probability:
        return None
    recent = {_identity(line) for line in recent_outputs if line}
    candidates = [
        line for line in _LINES[topic][style]
        if _identity(line) not in recent
    ]
    if not candidates:
        return None
    return rng.choice(candidates), topic


__all__ = [
    "PERSONALITY_STYLES",
    "PERSONALITY_STYLE_LABELS",
    "resolve_personality_style",
    "detect_literary_topic",
    "choose_personality_line",
]

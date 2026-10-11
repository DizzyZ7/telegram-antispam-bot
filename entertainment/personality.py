"""Fosgen Personality v1: opt-in, topic-grounded literary interjections.

No external AI, participant profiling, names, learned personal attributes or
cross-chat memory. A selected style is ONLY a writing tone requested by a user.
Generated lines are original fixed micro-scenes, never copied user messages.
"""
from __future__ import annotations

import random
import re
from difflib import SequenceMatcher
from collections.abc import Iterable

PERSONALITY_STYLES = frozenset({"gentle", "ironic", "absurd"})
PERSONALITY_STYLE_LABELS = {
    "gentle": "мягкий",
    "ironic": "ирония",
    "absurd": "абсурд",
}
PERSONALITY_TOPICS = frozenset({"writing", "reading", "characters", "covers"})
PERSONALITY_TOPIC_LABELS = {
    "writing": "главы", "reading": "чтение",
    "characters": "герои", "covers": "обложки",
}
_TOPIC_ALIASES = {
    "главы": "writing", "тексты": "writing", "писательство": "writing",
    "чтение": "reading", "книги": "reading", "фанфики": "reading",
    "герои": "characters", "персонажи": "characters", "сюжет": "characters",
    "обложки": "covers", "рисование": "covers", "арт": "covers",
}
_JOKE_REQUEST = re.compile(
    r"\b(?:пошути(?:те)?|шутк[уаиеой]|расскажи(?:те)?|сочини(?:те)?|"
    r"удиви(?:те)?|развесели(?:те)?)\b", re.IGNORECASE,
)
PERSONALITY_USER_COOLDOWN_SECONDS = 6 * 60 * 60
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
            "Может, сегодня история начнется с одной фразы. Этого достаточно для следующей страницы. 🌿",
        ),
        "ironic": (
            "Отдел дедлайнов сообщает: вдохновение снова явилось без пропуска и ушло через запасной выход.",
            "Черновик подал заявление о независимости. Автору предлагается переговорить с ним за чаем.",
            "Комиссия установила: слово «почти» в выражении «глава почти готова» юридически не ограничено.",
            "Редакторская служба измерила дедлайн: оказалось, он ускоряется, когда на него смотришь.",
        ),
        "absurd": (
            "Инспекция черновиков обнаружила портал в следующую главу. На входе требуют три метафоры.",
            "Литературный принтер объявил забастовку: требует отпуск для запятых и премию для тире.",
            "Пока автор ищет вдохновение, вдохновение пытается оформить автора в библиотеку.",
            "Муза заблудилась в оглавлении и требует спасательную экспедицию из трех черновиков.",
        ),
    },
    "reading": {
        "gentle": (
            "Некоторые истории хочется растянуть еще на вечер. Особенно когда впереди неизвестная глава. 📚",
            "Уютная читательская магия: «Еще страницу» превращается в целый вечер.",
            "Пусть следующая глава окажется именно той, ради которой хотелось вернуться. ✨",
            "В хорошей истории даже короткая пауза обещает что-то удивительное. 📖",
        ),
        "ironic": (
            "Эксперимент «не обновлять страницу с продолжением» снова завершился досрочно.",
            "Читательский совет признал: ждать продолжение труднее, чем читать предупреждения к главе.",
            "Обновление истории пока не обнаружено. Наблюдатели продолжают драматично смотреть на экран.",
            "Институт читательского терпения закрыт на ремонт после слов «скоро новая глава».",
        ),
        "absurd": (
            "Комитет читательского ожидания постановил построить маяк из закладок.",
            "В библиотеке обнаружен временной разлом: пять минут чтения равны двум часам.",
            "Система оповещения о новой главе включила фанфары и случайно разбудила книжного дракона.",
            "Дракон закладок снова перепутал главы с сокровищами. Экспедиция читателей собирается.",
        ),
    },
    "characters": {
        "gentle": (
            "Хороший персонаж иногда удивляет даже того, кто придумал ему судьбу. 🌙",
            "Если герои спорят с планом, возможно, у истории просто появился собственный голос.",
            "Самые интересные повороты обычно начинаются с маленького «а что, если».",
            "Даже второстепенному герою иногда нужна своя маленькая победа. 🌙",
        ),
        "ironic": (
            "Персонажи вызваны на планерку. Сюжет по-прежнему отказывается предъявлять документы.",
            "Отдел логики сюжета временно закрыт: главный герой опять пошел не туда.",
            "Злодей просит внести в контракт пункт о человеческом отношении к его мотивации.",
            "Герой потребовал показать ему весь сюжет. Юридический отдел автора хранит молчание.",
        ),
        "absurd": (
            "Сюжет вызвал полицию нравов: один персонаж самовольно украл чужую сюжетную арку.",
            "Главный герой открыл профсоюз второстепенных персонажей и назначил себя секретарем.",
            "Кульминация сбежала из третьего акта. Последний раз ее видели с чашкой чая.",
            "Второстепенный герой захватил повествование и объявил выходной всем эпилогам.",
        ),
    },
    "covers": {
        "gentle": (
            "Иногда один точный оттенок рассказывает о книге больше целой аннотации. 🎨",
            "Обложка начинается с настроения. Остальное художник обязательно приручит.",
            "Палитра тоже умеет рассказывать истории — только без единого слова.",
            "Настроение обложки находится между оттенками, которые никак не хотели знакомиться. 🎨",
        ),
        "ironic": (
            "Комиссия по обложкам требует, чтобы шрифт и фон наконец договорились о мирном сосуществовании.",
            "Цветовая палитра заявила: она не обязана соответствовать ожиданиям всех персонажей сразу.",
            "Худсовет постановил: еще один оттенок — и палитра получит собственную сюжетную линию.",
            "Совет шрифтов требует равных прав: заголовок опять забрал все внимание.",
        ),
        "absurd": (
            "На обложке обнаружен заговор оттенков. Фиолетовый назначен главным подозреваемым.",
            "Шрифт открыл портал в типографическое измерение. Возвращаться отказывается.",
            "Художник вызвал дипломатическую миссию для переговоров между цветами.",
            "В художественном отделе открыли дело о похищении пикселя. Следствие ведет кисточка.",
        ),
    },
}


def resolve_personality_style(raw: str) -> str | None:
    """Map explicit opt-in input to a style, never infer it from messages."""
    key = str(raw or "").strip().casefold()
    value = _STYLE_ALIASES.get(key, key)
    return value if value in PERSONALITY_STYLES else None


def resolve_personality_topic(raw: str) -> str | None:
    """Only user-selected topic preference; do not infer from biography."""
    key = str(raw or "").strip().casefold()
    topic = _TOPIC_ALIASES.get(key, key)
    return topic if topic in PERSONALITY_TOPICS else None


def _identity(text: str) -> str:
    return " ".join(re.findall(r"[a-zа-яё0-9]+", text.casefold()))


def _too_similar(line: str, recent: set[str]) -> bool:
    normalized = _identity(line)
    return normalized in recent or any(
        min(len(normalized), len(other)) >= 30
        and SequenceMatcher(None, normalized, other).ratio() >= 0.86
        for other in recent
    )


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
    preferred_topic: str | None = None,
) -> tuple[str, str] | None:
    """Optional direct-reply bonus, not a replacement for Culture Memory.

    Bounded style-specific catalog, exact-topic match, one roll per attempt.
    Existing bot output is never copied or decorated with user identity.
    """
    if style not in PERSONALITY_STYLES:
        return None
    topic = detect_literary_topic(trigger_text)
    if (
        topic is None
        and preferred_topic in PERSONALITY_TOPICS
        and isinstance(trigger_text, str)
        and len(trigger_text) <= 160
        and _JOKE_REQUEST.search(trigger_text)
    ):
        # Selected interest guides an explicit joke request only; if someone
        # mentions a different literary topic, the actual conversation wins.
        topic = preferred_topic
    if topic is None or rng.random() >= engagement_probability:
        return None
    recent = {_identity(line) for line in recent_outputs if line}
    candidates = [
        line for line in _LINES[topic][style]
        if not _too_similar(line, recent)
    ]
    if not candidates:
        return None
    return rng.choice(candidates), topic


__all__ = [
    "PERSONALITY_STYLES",
    "PERSONALITY_STYLE_LABELS",
    "PERSONALITY_TOPICS",
    "PERSONALITY_TOPIC_LABELS",
    "PERSONALITY_USER_COOLDOWN_SECONDS",
    "resolve_personality_topic",
    "resolve_personality_style",
    "detect_literary_topic",
    "choose_personality_line",
]

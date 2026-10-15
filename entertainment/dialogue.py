"""Local rule-based dialogue continuations for Fosgen; no models or API calls.

The first release intentionally handles only reliable reply intents. It never
splices arbitrary user text into bot messages and never stores user wording.
Unknown intent or sensitive contexts result in silence.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import Sequence

from .moderation_safety import is_safe_entertainment_reply, is_safe_entertainment_source

SESSION_SECONDS = 10 * 60
MAX_DIALOGUE_TURNS = 4
DIALOGUE_COOLDOWN_SECONDS = 3
DIALOGUE_BURST_SECONDS = 120
DIALOGUE_BURST_LIMIT = 6

_SCENE_PATTERNS = {
    "study": re.compile(r"учеб|уч[её]б|школ|студент|пар[аыу]|экзамен|зач[её]т|бухуч[её]т|препод|домашк|диплом|курсов|сесси|конспект", re.I),
    "writing": re.compile(r"глав|сюжет|роман|черновик|редактор|книг|писат|персонаж|концовк|абзац|рукопис|истори|эпилог|геро[йя]|текст", re.I),
    "coding": re.compile(r"код|баг|сервер|депло|коммит|тест|сборк|релиз|проект|разработ|ветк|программ|ошибк", re.I),
    "cats": re.compile(r"кот|кошк|енот|мяу|лапк|хвост", re.I),
    "waiting": re.compile(r"жд|ожидан|задерж|потом|скоро|дедлайн|таймер|очеред", re.I),
}

_STOP = re.compile(r"\b(?:стоп|хватит|помолчи|отстань|закончим|не\s+отвечай|прекрати|закрой\s+тему)\b", re.I)
_SENSITIVE = re.compile(r"\b(?:умер|умерла|похорон|насили|смерт|больниц|суицид|самоубийств|соболезн|панич|плохо\s+себя|мне\s+страшно|не\s+хочу\s+жить)\b", re.I)
_LINK = re.compile(r"https?://|www\.|t\.me/|@", re.I)
_NEXT = re.compile(r"\b(?:а\s+дальше|что\s+дальше|дальше|продолж|что\s+потом|ну\s+и\s+что|чем\s+кончилось|и\s+что\s+же)\b", re.I)
_WHY = re.compile(r"\b(?:почему|зачем|откуда|с\s+чего|как\s+так|из-за\s+чего)\b", re.I)
_LAUGH = re.compile(r"ахах|хаха|пха+ха|пхах|хех|лол|ржу|смешн|😂|🤣|😭", re.I)
_EXCITED_LAUGH = re.compile(r"(?:АХАХ|ХАХА|ПХАХ|ПХААХ|🤣|😂)", re.I)
_CAPS_LAUGH_LINES = (
    "АХАХАХ, все, это уже канон 😂",
    "ПХАХА, кажется, у этой шутки появился второй сезон.",
    "АХАХА, внезапно даже мой внутренний кот засмеялся.",
    "ПХАХАХ, стоп, я не был готов к такому повороту.",
)

_NO = re.compile(r"^(?:нет|неа|не\s+совсем|неправда|не\s+согласен|сомневаюсь)[!?.\s]*$", re.I)
_YES = re.compile(r"^(?:да|ага|точно|верно|согласен|конечно|ну\s+да|именно)[!?.\s]*$", re.I)
_THANKS = re.compile(r"^(?:спасибо|благодарю|спасибки)[!?.\s]*$", re.I)
_QUESTION = re.compile(r"\b(?:серьезно|правда|реально|что|как|когда|а\s+что|это\s+как)\b|\?", re.I)
_SLEEP = re.compile(r"\b(?:не\s+выспал(?:ся|ась|ись)|спал\s+ли|сонн(?:ый|ая|ое)|хоч(?:у|ется)\s+спать|как\s+спал|спишь)\b", re.I)
_AFFECTION = re.compile(r"\b(?:обожаю|люблю|молодец|красавчик|лапочк|милый|лучший|умничк)\b", re.I)
_RELATIONSHIP = re.compile(r"\b(?:наших\s+(?:с\s+тобой\s+)?отношени[йях]|между\s+нами\s+что|мы\s+с\s+тобой\s+(?:пара|вместе)|наш\s+роман)\b", re.I)
_MOTIF_RE = re.compile(r"\b(?:протокол\w*|следств\w*|комисси\w*|эпизод\w*|сери\w*|сезон\w*)\b", re.I)

# Authored reactions to unmistakable social intents. These are deliberately
# short, do not claim an actual romantic relationship, and are not synthesized
# from personal messages. The outgoing moderation gate still applies.
_SOCIAL_LINES: dict[str, tuple[str, ...]] = {
    "sleep": (
        "Похоже, мой внутренний будильник опять решил поработать за сценариста.",
        "Режим сна сегодня в бета-тесте. Исправления обещали к понедельнику.",
        "Сон ушел на перерыв, а чувство юмора осталось дежурить.",
        "Будильник явно проснулся раньше здравого смысла.",
    ),
    "affection": (
        "Уровень милоты в чате неожиданно вырос. Сохраняю момент.",
        "Это самая приятная ошибка в моей системе за сегодня.",
        "Кажется, сейчас даже мой сарказм немного смутился.",
        "Внутренний кот довольно мурчит. Подробности засекречены.",
    ),
    "relationship": (
        "Звучит как спин-офф, который чат уже готов смотреть.",
        "Так, это уже романтическая комедия с непредсказуемым финалом.",
        "Сценарий такой поворот не согласовал, но зрители явно за.",
        "Главное, чтобы титры не появились посреди разговора.",
    ),
}


# Each scenario reuses a coherent image, not token-by-token Markov synthesis.
_LINES: dict[str, dict[str, tuple[str, ...]]] = {
    "study": {
        "next": (
            "Следующая серия: тетрадь просит отпуск, а сессия не согласна.",
            "У конспектов вышел новый сезон. Студенты опять без спойлеров.",
            "Бухучет уже готовит сюжетный поворот с дебетом и кредитом.",
        ),
        "why": (
            "Потому что дедлайн и сон давно состоят в сложных отношениях.",
            "Преподавательская логика пока проходит независимую экспертизу.",
            "Расписание опять придумал человек, который никогда не хотел спать.",
        ),
        "laugh": (
            "Бухучет услышал смех и сразу добавил контрольную.",
            "Кажется, у сессии появился собственный комедийный жанр.",
            "Конспект тоже хотел посмеяться, но его открыли на экзамене.",
        ),
        "agree": (
            "Согласен. Дебет с кредитом хотя бы однажды могли бы встретиться.",
            "После такого даже расписание должно выдать выходной.",
            "Учебный план кивнул и сделал вид, что ничего не произошло.",
        ),
        "disagree": (
            "Ладно, пересдача у этой шутки без права на шпаргалку.",
            "Тогда переделаем ответ без кафедральной бюрократии.",
            "Пусть учебный план попробует объяснить свою позицию.",
        ),
        "question": (
            "Вот бы преподаватели раздавали подсказки так же щедро, как задания.",
            "Если найти логику в расписании, можно сразу писать диссертацию.",
            "На этот вопрос даже калькулятор задумчиво смотрит в окно.",
        ),
        "update": (
            "Бухучет без подготовки к экзамену — уже сюжет для триллера.",
            "Когда препод не готовит к экзамену, тревожность пишет конспект сама.",
            "Сессия опять делает вид, что у всех бесконечная батарейка.",
        ),
    },
    "writing": {
        "next": (
            "Следующая серия: восстание последнего абзаца.",
            "Редактор открыл вторую папку с неожиданными поворотами.",
            "Финальный босс снова притворился эпилогом.",
            "Черновик тихо запросил продолжение сезона.",
        ),
        "why": (
            "Потому что у сюжета собственный профсоюз.",
            "Черновик отказался подписывать мирный договор.",
            "Причины спрятаны в режиссерской версии главы.",
        ),
        "laugh": (
            "Редактор уже оформляет эту сцену как канон.",
            "На полях рукописи кто-то нарисовал овации.",
            "Сноски потребовали отдельную главу.",
        ),
        "agree": (
            "Это решение единогласно поддержали все запятые.",
            "Теперь осталось согласовать финал с персонажами.",
            "Совет рукописей одобрил продолжение.",
        ),
        "disagree": (
            "Принято. Черновик отправлен на повторное чтение.",
            "Тогда сюжет объявляет внеочередное заседание.",
            "Поворот отменен. Персонажи слегка удивлены.",
        ),
        "question": (
            "Версия редакции: сюжет ведет двойную жизнь.",
            "Расследование упирается в загадочный последний абзац.",
            "В черновике обнаружена еще одна секретная дверь.",
        ),
        "update": (
            "Сюжет явно решил жить по собственному расписанию.",
            "Главное, чтобы финал не потребовал отдельный паспорт.",
            "Герои снова переписали план без согласования.",
        ),
    },
    "coding": {
        "next": (
            "Следующая серия: тесты вышли на переговоры.",
            "Коммит потребовал отдельный трейлер.",
            "Впереди финальный босс: продакшен.",
            "Сервер готовит неожиданный сюжетный поворот.",
        ),
        "why": (
            "Потому что баг нашел себе постоянную прописку.",
            "Судя по всему, логам опять известна тайна.",
            "Это заговор переменных с неопределенным характером.",
        ),
        "laugh": (
            "Тесты посмеялись. Теперь немного тревожно.",
            "В логах появился смайлик. Расследование продолжается.",
            "Сборка тоже оценила шутку. Кажется.",
        ),
        "agree": (
            "Зафиксировано. Даже линтер не возражает.",
            "Подпись поставлена цифровой уткой.",
            "Этот коммит заслужил спокойный релиз.",
        ),
        "disagree": (
            "Тогда отправляем гипотезу на повторные тесты.",
            "Версия отклонена, расследование по логам продолжается.",
            "Патч снят с повестки до лучших времен.",
        ),
        "question": (
            "Ответ находится где-то между логами и легендой.",
            "Техническая комиссия подозревает драматичный импорт.",
            "У сервера есть объяснение, но оно в архиве.",
        ),
        "update": (
            "Задача открыла побочный квест без предупреждения.",
            "Логи уже приготовили драматическую музыку.",
            "Сборка внезапно получила главную роль.",
        ),
    },
    "cats": {
        "next": (
            "Следующая серия: коты делят должность директора.",
            "Еноты потребовали права на собственный кабинет.",
            "Лапки готовят независимую экспертизу.",
            "У хвостатых уже появился секретный план.",
        ),
        "why": (
            "Потому что совет лапок голосовал единогласно.",
            "Кошачий устав не требует объяснений.",
            "Председатель хвостов отказался давать комментарии.",
        ),
        "laugh": (
            "Один кот уже записал смех как служебный успех.",
            "Енот поставил печать лапкой.",
            "Хвосты передают овации из соседней комнаты.",
        ),
        "agree": (
            "Согласовано. Лапки поставили четыре подписи.",
            "Кошачий совет одобряет этот порядок.",
            "Енот бережно убрал протокол в тайник.",
        ),
        "disagree": (
            "Кошачий совет отправил решение на доработку.",
            "Енот запросил еще одну экспертизу.",
            "Лапки воздержались от голосования.",
        ),
        "question": (
            "Все вопросы направлены в министерство хвостов.",
            "Главный свидетель — кот. Он спит.",
            "Секрет хранится под ближайшей подушкой.",
        ),
        "update": (
            "Похоже, у лапок начался новый рабочий день.",
            "Коты снова устроили внеплановое совещание.",
            "Енот уже несет протокол в зубах.",
        ),
    },
    "waiting": {
        "next": (
            "Следующая серия начнется после победы над таймером.",
            "Ожидание подало заявку на продление.",
            "Время открыло еще один дополнительный уровень.",
            "Часы делают вид, что не слышат.",
        ),
        "why": (
            "Потому что у времени обеденный перерыв.",
            "Таймер отказался объяснять свои решения.",
            "Очередь решила попробовать себя в драматургии.",
        ),
        "laugh": (
            "Таймер тоже хихикнул и добавил минуту.",
            "Часы официально оценили атмосферу.",
            "Ожидание уже записало это в дневник.",
        ),
        "agree": (
            "Часы подтвердили протокол ожидания.",
            "Таймер кивнул, не меняя показаний.",
            "Очередь одобрительно зашелестела.",
        ),
        "disagree": (
            "Тогда таймер отправляется на пересмотр.",
            "Очередь принесла письменное возражение.",
            "Время получило официальный запрос.",
        ),
        "question": (
            "Этот вопрос передан дежурному хронометристу.",
            "Часы пока консультируются с календарем.",
            "Таймер оставил ответ на потом.",
        ),
        "update": (
            "Время явно выбрало неожиданный маршрут.",
            "Таймер приготовил новую сюжетную арку.",
            "Очередь обретает характер.",
        ),
    },
    "general": {
        "next": (
            "У этой истории уже появился второй сезон.",
            "Продолжение внесено в протокол странностей.",
            "Следующий эпизод обещает необычную развязку.",
            "Это явно не финальная сцена.",
        ),
        "why": (
            "Потому что абсурд тоже требует объяснительную.",
            "Причины скрыты в секретном приложении к протоколу.",
            "Это один из вопросов для комиссии по неожиданностям.",
        ),
        "laugh": (
            "В протокол внесены внеплановые овации.",
            "Это уже звучит как название отдельного эпизода.",
            "Совет случайностей доволен результатом.",
        ),
        "agree": (
            "Единогласно. Печать поставлена невидимыми чернилами.",
            "Это решение достойно отдельной сноски.",
            "Комиссия странностей утвердила итог.",
        ),
        "disagree": (
            "Возражение принято. Комиссия ушла перечитывать протокол.",
            "Тогда придется открыть альтернативную концовку.",
            "Эта версия возвращается на доработку.",
        ),
        "question": (
            "У этой загадки пока слишком уверенное выражение лица.",
            "В протоколе ответа нет, зато есть новая интрига.",
            "Следствие подозревает неожиданный поворот.",
        ),
        "update": (
            "Вот так обычно и начинается новая сюжетная арка.",
            "У событий явно собственный сценарист.",
            "Это заслуживает отдельной главы в хрониках.",
        ),
    },
}


@dataclass(frozen=True, slots=True)
class DialogueChoice:
    text: str
    scene: str
    intent: str
    stage: int


def infer_scene(text: str | None) -> str:
    if not isinstance(text, str):
        return "general"
    for scene, regex in _SCENE_PATTERNS.items():
        if regex.search(text.casefold()):
            return scene
    return "general"


def classify_reply(text: str | None) -> str | None:
    if not isinstance(text, str):
        return None
    value = " ".join(text.casefold().split())
    if not value or len(value) > 220 or value.startswith("/") or _LINK.search(value):
        return None
    if not is_safe_entertainment_source(value):
        return None
    if _STOP.search(value) or _SENSITIVE.search(value):
        return "silence"
    # Prefer a concrete social response over generic question templates;
    # reply intent is recognized only after opt-out and safety checks.
    if _RELATIONSHIP.search(value):
        return "relationship"
    if _SLEEP.search(value):
        return "sleep"
    if _AFFECTION.search(value):
        return "affection"
    if _NEXT.search(value):
        return "next"
    if _WHY.search(value):
        return "why"
    if _LAUGH.search(value):
        return "laugh"
    if _NO.fullmatch(value):
        return "disagree"
    if _YES.fullmatch(value):
        return "agree"
    if _THANKS.fullmatch(value):
        return "silence"
    if _QUESTION.search(value):
        return "question"
    if infer_scene(value) != "general":
        return "update"
    return None


def all_curated_lines() -> tuple[str, ...]:
    authored = (text for groups in _LINES.values() for lines in groups.values() for text in lines)
    return (
        *authored,
        *(text for lines in _SOCIAL_LINES.values() for text in lines),
        *_CAPS_LAUGH_LINES,
    )


def choose_continuation(
    *,
    incoming_text: str,
    previous_text: str,
    previous_scene: str | None,
    previous_stage: int,
    recent_outputs: Sequence[str],
    rng: random.Random,
) -> DialogueChoice | None:
    """Reply only to a known intent and after a limited number of turns."""
    intent = classify_reply(incoming_text)
    if intent is None or intent == "silence" or previous_stage >= MAX_DIALOGUE_TURNS:
        return None
    # Explicit topic in the new message can steer an ongoing conversation;
    # otherwise retain the previous storyline rather than mixing subjects.
    new_scene = infer_scene(incoming_text)
    scene = new_scene if new_scene != "general" else (
        previous_scene if previous_scene in _LINES else infer_scene(previous_text)
    )
    if scene not in _LINES:
        scene = "general"
    options = _SOCIAL_LINES[intent] if intent in _SOCIAL_LINES else _LINES[scene][intent]
    recent = {" ".join(value.casefold().split()) for value in recent_outputs}
    candidates = [
        line for line in options
        if " ".join(line.casefold().split()) not in recent
        and is_safe_entertainment_reply(line)
    ]
    if not candidates:
        return None
    # "Протокол / следствие / эпизод" is part of Fosgen's personality,
    # but repeated motifs in every reply make the conversation robotic.
    # Prefer a different motif when available; keep silence as fallback.
    used_motifs = {
        motif.group().casefold()[:5]
        for text in recent_outputs[-8:]
        for motif in _MOTIF_RE.finditer(text)
    }
    diverse = [
        line for line in candidates
        if not any(
            motif.group().casefold()[:5] in used_motifs
            for motif in _MOTIF_RE.finditer(line)
        )
    ]
    if diverse:
        candidates = diverse
    # Echo the chat's excitement occasionally, never on every laugh.
    # A remembered caps-laugh from the recent bot history prevents bursts.
    # All candidates pass the same output safety checks as ordinary phrases.
    recent_loud = any(
        _EXCITED_LAUGH.search(text) and text[:10].isupper()
        for text in recent_outputs[-8:]
    )
    if (
        intent == "laugh"
        and _EXCITED_LAUGH.search(incoming_text)
        and not recent_loud
        and rng.random() < 0.24
    ):
        excited = [
            line for line in _CAPS_LAUGH_LINES
            if " ".join(line.casefold().split()) not in recent
            and is_safe_entertainment_reply(line)
        ]
        if excited:
            return DialogueChoice(rng.choice(excited), scene, intent, previous_stage + 1)
    # Change style across turns but retain the same scene; randomness only
    # selects from original fully authored, independently safety-checked lines.
    chosen = rng.choice(candidates)
    return DialogueChoice(chosen, scene, intent, previous_stage + 1)


__all__ = [
    "SESSION_SECONDS", "MAX_DIALOGUE_TURNS", "DIALOGUE_COOLDOWN_SECONDS",
    "DIALOGUE_BURST_SECONDS", "DIALOGUE_BURST_LIMIT", "DialogueChoice",
    "infer_scene", "classify_reply", "choose_continuation", "all_curated_lines",
]

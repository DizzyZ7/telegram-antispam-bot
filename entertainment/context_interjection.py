"""Topic-local, consent-respecting contextual entrances for Fosgen.

No NLP providers and no arbitrary quote/Markov synthesis. The bot may join an
ongoing discussion when two distinct humans recently discussed the same
recognizable, harmless theme. The existing presence/autonomy decision must
*already* have approved a spontaneous action; this module never schedules one.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import Sequence

from .dialogue import infer_scene
from .models import EntertainmentActionRecord, MemoryEvent, MemoryEventType
from .moderation_safety import is_safe_entertainment_reply, is_safe_entertainment_source

CONTEXT_WINDOW_SECONDS = 4 * 60
LATEST_HUMAN_SECONDS = 90
REPEAT_SECONDS = 12 * 60
SCOPED_EVENT_LIMIT = 45
_ACTION_SOURCE = "context_interjection_v1"
_SENSITIVE = re.compile(
    r"\b(?:умер|похорон|самоубийств|суицид|насили|помогите|"
    r"угроз|оскорблен|ссора|травл|больниц|болезн|"
    r"ненавиж|страшно|развод|скорб|депресси|"
    r"не\s+хочу\s+жить|заткни|мошенник)\w*\b",
    re.I,
)

# Gentle, conversational scene additions, not accusations or statements
# about individuals. Designed to elicit more discussion, not drown it out.
_SCENE_LINES: dict[str, tuple[str, ...]] = {
    "writing": (
        "У этой истории уже ощущается собственный режиссер.",
        "Интересно, автор сюжета уже знает, чем все закончится?",
        "Кажется, персонажи опять получили право голоса.",
        "У рукописи явно есть еще одна скрытая дверь.",
        "Вот так начинаются легенды о последнем абзаце.",
        "Если главы объявят независимость, удивления не будет.",
        "Сюжет сейчас выглядит как очень перспективный сериал.",
        "Литературный совет в ожидании следующего поворота.",
    ),
    "coding": (
        "Кажется, у этого проекта открылся новый побочный квест.",
        "Интересно, тесты уже знают о таком развитии событий?",
        "У каждого релиза когда-нибудь появляется свой сюжет.",
        "Технический совет запрашивает попкорн и логи.",
        "Похоже, даже отладчик следит за продолжением.",
        "Программистская хроника получила очередную главу.",
        "Если сервер заговорит, это будет отдельный релиз.",
        "У этого патча явно есть собственная предыстория.",
    ),
    "cats": (
        "Кошачий совет требует созвать внеочередное собрание.",
        "А кто-нибудь уже спросил мнение главного кота?",
        "Еноты явно хотят участвовать в этом обсуждении.",
        "В министерстве лапок такое не оставили бы без протокола.",
        "Похоже, хвостатые опять ведут тайные переговоры.",
        "Заседание кошачьего комитета объявляется открытым.",
        "Одобрение лапками еще предстоит получить.",
        "Судьба вопроса теперь в надежных пушистых лапах.",
    ),

    "games": (
        "Кажется, эта катка уже просится в отдельную легенду.",
        "Квест явно решил открыть секретную концовку.",
        "Вот бы у следующего босса было чувство юмора.",
        "У этого уровня явно личные планы на вечер.",
        "Игровой совет требует повторный просмотр записи.",
        "Кажется, достижения пишут собственную историю.",
    ),
    "music": (
        "Этот разговор уже звучит как идея для альбома.",
        "Интересно, какой трек получит финальные титры?",
        "Кажется, плейлист попросил внеочередное обновление.",
        "Музыкальный совет требует еще один припев.",
        "У этой мелодии явно есть скрытая сцена.",
        "Саундтрек к обсуждению уже напрашивается.",
    ),
    "art": (
        "Кажется, палитра сегодня сама выбирает сюжет.",
        "У обложки уже ощущается собственная предыстория.",
        "Художественный совет требует еще один эскиз.",
        "Это обсуждение просится в подпись под картиной.",
        "Кисточки явно готовят внеочередное совещание.",
        "У иллюстрации явно есть запасной план.",
    ),
    "movies": (
        "У этого обсуждения явно есть сцена после титров.",
        "Кажется, сценаристы еще берегут главный поворот.",
        "Совет зрителей требует дополнительную серию.",
        "Интересно, будет ли продолжение у этой истории?",
        "У кинозала уже накопились теории.",
        "Судя по атмосфере, это только первый акт.",
    ),
    "study": (
        "Конспект официально запросил выходной.",
        "Кажется, у экзамена появился собственный сюжет.",
        "Учебный совет просит еще одну чашку чая.",
        "Эта тема явно готовит бонусный вопрос.",
        "Зачетка притворяется, что все под контролем.",
        "Лекция явно планирует неожиданный финал.",
    ),
    "food": (
        "Кулинарный совет требует дегустацию.",
        "Кажется, рецепт решил стать главным героем.",
        "У кухни сегодня явно насыщенная сюжетная линия.",
        "Над этим обсуждением незримо витает аромат ужина.",
        "Шеф-повар воображаемого ресторана одобрительно кивает.",
        "Кажется, десерт готовит неожиданный поворот.",
    ),
    "travel": (
        "Маршрут явно хочет добавить секретную остановку.",
        "У этой поездки уже назревает отдельная глава.",
        "Совет путешественников запросил карту приключений.",
        "Кажется, чемодан готовится к собственной истории.",
        "У вокзала всегда найдется запасной сюжет.",
        "Вот так обычно и начинаются рассказы об отпуске.",
    ),
    "waiting": (
        "Кажется, у таймера свое представление о драматургии.",
        "Интересно, сколько серий еще продлится ожидание?",
        "Время сегодня выступает независимым персонажем.",
        "Совет ожидания официально запросил продолжение.",
        "Таймер, похоже, готовит сюжетный поворот.",
        "У слова «скоро» сегодня подозрительно длинная роль.",
        "Часы делают вид, что не участвуют в обсуждении.",
        "Ожидание уже заслужило отдельную заставку.",
    ),
}

# Explicit, conservative local subject cues. Unlike the Dialogue Engine these
# can only open a spontaneous remark after TWO people match the same scene.
_EXTRA_SCENE_PATTERNS: dict[str, re.Pattern[str]] = {
    "games": re.compile(r"\b(?:игр\w*|уровн\w*|босс\w*|рейд\w*|покемон\w*|катк\w*|квест\w*|прохожд\w*)\b", re.I),
    "music": re.compile(r"\b(?:музык\w*|песн\w*|трек\w*|альбом\w*|мелоди\w*|концерт\w*|плейлист\w*|гитар\w*|припев\w*)\b", re.I),
    "art": re.compile(r"\b(?:рисун\w*|рисую|рисует|иллюстрац\w*|обложк\w*|эскиз\w*|палитр\w*|картин\w*)\b", re.I),
    "movies": re.compile(r"\b(?:фильм\w*|сериал\w*|кин\w*|актер\w*|актрис\w*|сезон\w*|сценари\w*)\b", re.I),
    "study": re.compile(r"\b(?:экзамен\w*|урок\w*|зачет\w*|лекци\w*|конспект\w*|учеб\w*|курсов\w*|семинар\w*)\b", re.I),
    "food": re.compile(r"\b(?:готовл\w*|рецепт\w*|запек\w*|пицц\w*|суп\w*|торт\w*|кофе|ча[йяею]|кухн\w*|ужин\w*)\b", re.I),
    "travel": re.compile(r"\b(?:поездк\w*|путешеств\w*|билет\w*|вокзал\w*|самолет\w*|аэропорт\w*|отпуск\w*|маршрут\w*|отел\w*)\b", re.I),
}


def detect_discussion_scene(text: str) -> str:
    existing = infer_scene(text)
    if existing != "general":
        return existing
    for scene, expression in _EXTRA_SCENE_PATTERNS.items():
        if expression.search(text):
            return scene
    return "general"



@dataclass(frozen=True, slots=True)
class ContextInterjection:
    text: str
    scene: str


def choose_context_interjection(
    events: Sequence[MemoryEvent],
    *,
    now: int,
    recent_actions: Sequence[EntertainmentActionRecord],
    recent_outputs: Sequence[str],
    rng: random.Random,
) -> ContextInterjection | None:
    if not events:
        return None
    if any(
        action.metadata.get("source") == _ACTION_SOURCE
        and 0 <= now - int(action.created_at) < REPEAT_SECONDS
        for action in recent_actions
    ):
        return None

    scope = (int(events[-1].chat_id), int(events[-1].topic_id))
    relevant: list[tuple[int, int, str]] = []
    for event in events[-SCOPED_EVENT_LIMIT:]:
        if (int(event.chat_id), int(event.topic_id)) != scope:
            continue
        if event.sender_is_bot or event.is_forwarded or event.is_command:
            continue
        if event.event_type not in {MemoryEventType.TEXT, MemoryEventType.EMOJI}:
            continue
        age = now - int(event.created_at)
        if not 0 <= age <= CONTEXT_WINDOW_SECONDS:
            continue
        text = event.text
        if not isinstance(text, str) or not 8 <= len(text) <= 400:
            continue
        if not is_safe_entertainment_source(text) or _SENSITIVE.search(text):
            # Don't insert jokes in a live discussion with evidence of distress.
            return None
        if text.lstrip().startswith("/") or "http://" in text or "https://" in text:
            continue
        scene = detect_discussion_scene(text)
        if scene != "general":
            relevant.append((int(event.created_at), int(event.user_id), scene))

    if not relevant:
        return None
    relevant.sort(key=lambda item: item[0])
    last_at, _user, last_scene = relevant[-1]
    if now - last_at > LATEST_HUMAN_SECONDS:
        return None
    speakers = {uid for _at, uid, scene in relevant if scene == last_scene}
    if len(speakers) < 2:
        return None
    recent = {" ".join(text.casefold().split()) for text in recent_outputs}
    candidates = [
        line for line in _SCENE_LINES[last_scene]
        if " ".join(line.casefold().split()) not in recent
        and is_safe_entertainment_reply(line)
    ]
    return ContextInterjection(rng.choice(candidates), last_scene) if candidates else None


def authored_interjections() -> tuple[str, ...]:
    return tuple(line for options in _SCENE_LINES.values() for line in options)


__all__ = [
    "CONTEXT_WINDOW_SECONDS", "LATEST_HUMAN_SECONDS", "REPEAT_SECONDS",
    "ContextInterjection", "choose_context_interjection", "authored_interjections",
    "detect_discussion_scene",
]

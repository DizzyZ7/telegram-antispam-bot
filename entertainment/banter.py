"""Safe, topic-grounded conversational recovery for Fosgen.

Generation v3 remains the default. This catalog is used only when an already
approved action cannot yield a safe original sentence. It never learns a
personality profile, quotes a participant or wakes an inactive topic.
"""
from __future__ import annotations

import random
import re
from collections.abc import Iterable, Sequence
from difflib import SequenceMatcher

from .models import MemoryEvent, MemoryEventType

_FRESH_CONTEXT_SECONDS = 20 * 60
_RECENT_CONTEXT_LIMIT = 24
_CONTEXT_KINDS = frozenset({MemoryEventType.TEXT, MemoryEventType.EMOJI, MemoryEventType.PHOTO})
_LINK_RE = re.compile(r"(?:https?://|www\.|t\.me/)", re.I)
_PATTERNS = {
    "waiting": re.compile(
        r"\b(?:жду|ждем|ждём|ждал(?:а|и)?|ожидани\w*|когда\s+уже|скоро\s+будет)\b", re.I
    ),
    "celebration": re.compile(
        r"\b(?:ура|наконец(?:-то)?|готово|получилось|заработало|победа|сделано)\b", re.I
    ),
    "technical": re.compile(
        r"\b(?:баг(?:и|ов|а)?|глючит|сломалось|сломался|не\s+работает|ошибка|фикс(?:ить|им|нул|нули)?)\b", re.I
    ),
    "laughter": re.compile(r"\b(?:ахах\w*|хах\w*|ржу|лол|кек|смешно)\b", re.I),
    "choice": re.compile(
        r"\b(?:выбрать|выбираю|выбор|вариант\w*|какой\s+лучше|что\s+выбрать)\b", re.I
    ),
}
# Original, non-personal micro-jokes. No claims about any participant or
# specific real-world event. The output is checked by existing moderation.
_LINES = {
    "waiting": (
        "Ожидание уже претендует на отдельную сюжетную линию.",
        "Кажется, у слова «скоро» сегодня очень гибкое расписание.",
        "Часы делают вид, что они здесь вообще ни при чем.",
        "Терпение запросило отпуск. Заявление пока на рассмотрении.",
        "Скорость событий сейчас измеряется в чайных ложках.",
        "Время решило добавить немного драматической паузы.",
    ),
    "celebration": (
        "Фиксируем исторический момент: оно все-таки случилось.",
        "Торжественно объявляю режим «ну наконец-то» открытым.",
        "Победа! Можно пять минут не воевать с реальностью.",
        "Комиссия по чудесам просит приложить скриншот.",
        "Событие настолько редкое, что хочется сохранить лог.",
        "Где-то в параллельной вселенной уже аплодируют.",
    ),
    "technical": (
        "Система сделала вид, что так и было задумано.",
        "У реальности снова вышел патч без списка изменений.",
        "Кажется, баг уже зарегистрировал собственный баг-трекер.",
        "Логи молчат с очень подозрительным достоинством.",
        "Сегодня даже отладчик нуждается в эмоциональной поддержке.",
        "У этой ошибки явно был план на вечер.",
    ),
    "laughter": (
        "Мне кажется, этот момент заслужил отдельные титры.",
        "Смех официально вышел из-под контроля.",
        "Пытаюсь восстановить серьезное выражение лица. Безуспешно.",
        "Здравый смысл попросил отгул до завтра.",
        "По уровню хаоса все идет подозрительно хорошо.",
        "У этого диалога явно есть режиссер комедии.",
    ),
    "choice": (
        "Кажется, обоим вариантам нужна драматическая музыка.",
        "Совет сомневающихся запросил еще один вариант.",
        "Можно назначить третий вариант главным подозреваемым.",
        "Выбор выглядит как финал сезона без подсказки зрителям.",
        "Комитет решений объявил совещание бессрочным.",
        "У дилеммы внезапно появились собственные амбиции.",
    ),
}


def detect_banter_topic(text: str | None) -> str | None:
    """Only high-confidence, harmless conversational motifs."""
    if not isinstance(text, str):
        return None
    clean = " ".join(text.split()).strip()
    if not 8 <= len(clean) <= 300 or _LINK_RE.search(clean) or clean.startswith("/"):
        return None
    for topic, pattern in _PATTERNS.items():
        if pattern.search(clean):
            return topic
    return None


def recent_banter_context(events: Sequence[MemoryEvent], *, now: int) -> str | None:
    """Use only fresh human context from the first event's chat AND topic."""
    if not events:
        return None
    scope = (int(events[0].chat_id), int(events[0].topic_id))
    for event in reversed(events[-_RECENT_CONTEXT_LIMIT:]):
        if (int(event.chat_id), int(event.topic_id)) != scope:
            continue
        if event.sender_is_bot or event.is_forwarded or event.is_command:
            continue
        if event.event_type not in _CONTEXT_KINDS:
            continue
        age = int(now) - int(event.created_at)
        if not 0 <= age <= _FRESH_CONTEXT_SECONDS:
            continue
        text = (
            event.text if event.event_type in {MemoryEventType.TEXT, MemoryEventType.EMOJI}
            else event.caption
        )
        if detect_banter_topic(text):
            return text
    return None


def _identity(text: str) -> str:
    return " ".join(re.findall(r"[a-zа-яё0-9]+", text.casefold()))


def _too_similar(line: str, recent: set[str]) -> bool:
    candidate = _identity(line)
    return candidate in recent or any(
        min(len(candidate), len(previous)) >= 25
        and SequenceMatcher(None, candidate, previous).ratio() >= 0.84
        for previous in recent
    )


def choose_banter_line(
    *,
    trigger_text: str | None,
    recent_outputs: Iterable[str],
    rng: random.Random,
) -> tuple[str, str] | None:
    """Return an original line + non-personal topic label, or stay silent."""
    topic = detect_banter_topic(trigger_text)
    if topic is None:
        return None
    recent = {_identity(output) for output in recent_outputs if isinstance(output, str) and output}
    options = [line for line in _LINES[topic] if not _too_similar(line, recent)]
    if not options:
        return None
    return rng.choice(options), topic


__all__ = ["choose_banter_line", "detect_banter_topic", "recent_banter_context"]

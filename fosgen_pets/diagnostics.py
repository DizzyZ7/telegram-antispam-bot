"""Bounded operator-activated metadata tracing; never retain Message or text."""
from dataclasses import dataclass, replace
from datetime import datetime
import logging
from zoneinfo import ZoneInfo

from aiogram import BaseMiddleware
from aiogram.types import Message

LOGGER = logging.getLogger(__name__)


def trace_call(trace, method, *args, **kwargs):
    """Observability is optional; its failure cannot change moderation/counting."""
    if trace is None:
        return None
    try:
        return getattr(trace, method)(*args, **kwargs)
    except Exception as exc:
        LOGGER.warning("FOSGEN_PETS_TRACE_FAILED reason=%s", type(exc).__name__)
        return None


@dataclass(frozen=True)
class MessageObservation:
    chat_id: int
    thread_id: int | None
    message_id: int
    source_date: datetime
    reason: str = "received"
    handler: str = ""
    error_type: str = ""


class PetMessageTrace:
    def __init__(self):
        self._admins = frozenset()
        self._enabled = set()
        self._latest = {}

    def configure(self, admins: frozenset[int]) -> None:
        self._admins = admins
        self._enabled.intersection_update(admins)
        self._latest = {owner: entry for owner, entry in self._latest.items() if owner in self._enabled}

    def enable(self, owner: int) -> None:
        if owner in self._admins:
            self._enabled.add(owner)

    def disable(self, owner: int) -> None:
        self._enabled.discard(owner)
        self._latest.pop(owner, None)

    def get(self, owner: int) -> MessageObservation | None:
        return self._latest.get(owner)

    def begin(self, event: Message) -> None:
        user = event.from_user
        if user and user.id in self._enabled and event.chat.type in ("group", "supergroup"):
            self._latest[user.id] = MessageObservation(
                event.chat.id, event.message_thread_id, event.message_id, event.date)

    def mark(self, event: Message, reason: str, *, handler: str = "", error_type: str = "") -> None:
        user = event.from_user
        entry = self._latest.get(user.id) if user and user.id in self._enabled else None
        if entry and (entry.chat_id, entry.message_id) == (event.chat.id, event.message_id):
            # A slow older handler cannot replace the most recently received event.
            self._latest[user.id] = replace(entry, reason=reason,
                handler=handler[:80] or entry.handler, error_type=error_type[:80])


class PetDiagnosticIngress(BaseMiddleware):
    """Installed before moderation; its outcome and exceptions remain unchanged."""
    def __init__(self, trace: PetMessageTrace):
        self.trace = trace

    async def __call__(self, handler, event: Message, data):
        trace_call(self.trace, "begin", event)
        try:
            result = await handler(event, data)
        except Exception as exc:
            trace_call(self.trace, "mark", event, "pipeline_error", error_type=type(exc).__name__)
            raise
        entry = trace_call(self.trace, "get", event.from_user.id) if event.from_user else None
        if entry and (entry.chat_id, entry.message_id) == (event.chat.id, event.message_id) and entry.reason == "received":
            trace_call(self.trace, "mark", event, "blocked_before_handler")
        return result


REASONS = {
    "received": "сообщение получено, обработка еще идет",
    "candidate": "прошло фильтр, ожидается результат хранилища",
    "counted": "сообщение засчитано",
    "outside_scope": "этот чат не подключен к питомцам",
    "not_allowed": "чат исключен основным allowlist",
    "lexicon_only": "для чата включен только Лексикон",
    "pending_captcha": "пользователь еще проходит проверку",
    "sender_not_personal": "сообщение бота или анонимного отправителя",
    "not_original": "пересылка, правка или сообщение через бота",
    "future_timestamp": "дата Telegram опережает часы бота более чем на минуту",
    "text_ineligible": "текст не проходит требования: 2 слова, 8 символов, без команды",
    "different_handler": "сообщение принял другой обработчик",
    "blocked_before_handler": "обработка остановлена до счетчика: модерация или другой внешний фильтр",
    "pipeline_error": "ошибка основного обработчика",
    "storage_error": "ошибка хранилища питомцев",
    "consent_missing": "согласие для этого аккаунта отсутствует",
    "consent_paused": "учет приостановлен",
    "before_consent": "дата сообщения раньше сохраненного согласия",
    "already_unlocked": "100 сообщений уже набраны",
    "replayed_update": "это обновление Telegram уже обработано",
    "repeated_text": "такой текст уже был засчитан",
    "cooldown": "между зачтенными сообщениями прошло менее 30 секунд",
    "not_counted": "хранилище вернуло отказ без подробной причины",
}


def format_observation(entry: MessageObservation | None) -> str:
    if entry is None:
        return ("Диагностика твоих новых групповых сообщений включена. Отправь один новый "
                "текст от своего аккаунта в группе, затем снова /pet_status. "
                "Анонимное сообщение нельзя связать с твоим аккаунтом.")
    stamp = entry.source_date.astimezone(ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y %H:%M:%S МСК")
    return (f"Последнее твое групповое сообщение: {entry.message_id}\n"
            f"Чат: {entry.chat_id}\nТема: {entry.thread_id if entry.thread_id is not None else 'общая'}\n"
            f"Время Telegram: {stamp}\nОбработчик: {entry.handler or 'не достигнут'}\n"
            f"Результат: {REASONS.get(entry.reason, 'неизвестно')} ({entry.reason})"
            + (f"\nТип ошибки: {entry.error_type}" if entry.error_type else ""))

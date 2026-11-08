"""Bounded operational search results; no text, credentials or user profiles."""
from __future__ import annotations

import math
from collections import OrderedDict
from dataclasses import dataclass

from .models import normalize_topic_id

_REASONS = {
    'ok': 'Ответ принят',
    'missing_answer': 'Tavily не прислал текст ответа',
    'answer_format': 'Неподдерживаемый формат ответа',
    'answer_too_long': 'Ответ длиннее 700 символов',
    'answer_links': 'В ответе есть ссылки или HTML',
    'answer_language': 'Ответ не прошел проверку русского языка',
    'answer_safety': 'Ответ не прошел проверку безопасности',
    'missing_results': 'Нет подходящих поисковых результатов',
    'unsupported_answer': 'Ответ не прошел проверку связи с источниками',
    'invalid_request': 'Пустой или слишком длинный вопрос',
    'http_auth': 'Tavily не принял ключ',
    'http_access': 'Tavily отказал в доступе',
    'http_request': 'Tavily отклонил параметры запроса',
    'http_rate_limit': 'Превышена частота запросов Tavily',
    'http_quota': 'Достигнут лимит использования Tavily',
    'http_capacity': 'Нет свободной мощности на текущем плане Tavily',
    'http_provider': 'Ошибка сервера Tavily',
    'http_error': 'Неуспешный HTTP-ответ',
    'response_size': 'HTTP-ответ больше 128 КиБ',
    'timeout': 'Истек лимит времени ожидания поиска',
    'transport': 'Ошибка соединения с Tavily',
    'response_format': 'Не удалось разобрать HTTP-ответ',
    'throttled': 'Поиск занят или действует ограничение 30 секунд',
    'provider_failure': 'Нераспознанный отказ поиска',
    'unavailable': 'Непредвиденная ошибка поиска',
    'claim_unavailable': 'Не удалось подтвердить право на единственную отправку',
}


def safe_search_reason(reason: str) -> str:
    return reason if reason in _REASONS else 'provider_failure'


@dataclass(frozen=True, slots=True)
class SearchDiagnostic:
    outcome: str
    reason: str
    http_status: int | None
    elapsed_ms: int
    delivery: str


class SearchDiagnostics:
    def __init__(self) -> None:
        self._last: OrderedDict[tuple[int, int], SearchDiagnostic] = OrderedDict()

    def record(self, chat_id: int, topic_id: int | None, *, outcome: str, reason: str,
               http_status: object, elapsed_ms: float, delivery: str) -> None:
        key = (int(chat_id), normalize_topic_id(topic_id))
        status = http_status if type(http_status) is int and 100 <= http_status <= 599 else None
        duration = max(0, min(120000, int(elapsed_ms))) if math.isfinite(elapsed_ms) else 0
        result = SearchDiagnostic(
            outcome if outcome in {'success', 'fallback', 'skipped'} else 'skipped',
            safe_search_reason(reason), status, duration,
            delivery if delivery in {'sent', 'uncertain', 'not_sent'} else 'uncertain',
        )
        self._last[key] = result
        self._last.move_to_end(key)
        if len(self._last) > 256:
            self._last.popitem(last=False)
        # Same output channel as startup diagnostics, independently of INFO
        # filtering or whether the host UI displays Python's stderr logger.
        print(f'FOSGEN_SEARCH_RESULT outcome={result.outcome} reason={result.reason} '
              f'http_status={result.http_status or "none"} elapsed_ms={result.elapsed_ms} '
              f'delivery={result.delivery}', flush=True)

    def status_text(self, chat_id: int, topic_id: int | None, *, enabled: bool) -> str:
        key = (int(chat_id), normalize_topic_id(topic_id))
        header = f'Поиск Tavily · диагностика\nКлюч настроен: {"да" if enabled else "нет"}\n'
        result = self._last.get(key)
        if result is None:
            return header + 'В этой теме с запуска процесса нет данных.'
        self._last.move_to_end(key)
        return (header + f'Последний результат: {result.outcome}\n'
                f'Причина: {result.reason} — {_REASONS[result.reason]}\n'
                f'HTTP: {result.http_status or "не получен"}\n'
                f'Длительность попытки: {result.elapsed_ms} мс\n'
                f'Доставка: {result.delivery}')

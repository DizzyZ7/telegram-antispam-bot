"""Allowlisted, content-free support diagnostics. Never forward raw client data."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

PLATFORMS = {"android", "android_x", "ios", "tdesktop", "macos", "web", "weba", "webk", "unigram", "unknown"}
OPERATIONS = {"session", "list", "create", "save", "open", "history", "submit", "upload", "delete_file", "withdraw", "revision", "other"}
ERROR_CODES = {
    "ok", "request_failed", "network_error", "request_timeout", "invalid_response",
    "unauthorized", "telegram_auth_invalid", "session_restored_unconfirmed",
    "not_found", "conflict", "payload_too_large", "rate_limited", "validation_error",
    "database_unavailable", "expected_version_required", "idempotency_key_required",
    "idempotency_key_invalid", "telegram_file_staging_unavailable", "eligibility_unavailable",
    "not_eligible", "bad_request", "forbidden", "origin_forbidden", "internal_error",
    "upstream_unavailable", "service_unavailable", "gateway_timeout", "http_error",
    "upload_network_error", "upload_timeout", "upload_invalid_response", "upload_aborted",
    "client_error", "client_validation_failed", "telegram_unavailable", "init_data_missing",
}
FIELDS = {
    "title", "work_type", "genre", "description", "body_text", "external_url", "details",
    "extra_links", "main_genres", "warnings", "palette_colors", "cover_file_id", "file",
    "fandom", "rating", "direction", "sketch_origin", "sketch_size", "sketch_collection_url",
    "sketch_profile_url", "sketch_article_url", "size_words", "pages", "parts", "unknown",
}
STATUSES = {"DRAFT", "SUBMITTED", "IN_REVIEW", "APPROVED", "CHANGES_REQUESTED", "REJECTED", "WITHDRAWN"}
RULES = {"unknown", "description_sentences", "description_words", "sketch_description_length",
         "file_count", "file_size", "file_extension", "file_type_mismatch", "file_empty",
         "file_invalid", "file_encoding", "genre_count", "duplicates", "warnings_selection",
         "https_required", "required", "max_length"}
_VERSION = re.compile(r"[0-9]{1,4}(?:\.[0-9]{1,4}){0,3}\Z")
_REFERENCE = re.compile(r"[a-fA-F0-9]{12}\Z")


def _choice(value: Any, allowed: set[str], default: str = "unknown") -> str:
    return value if isinstance(value, str) and value in allowed else default


def _number(value: Any, maximum: int) -> int:
    return value if type(value) is int and 0 <= value <= maximum else 0


def _flag(value: Any) -> str:
    return "да" if value is True else "нет" if value is False else "неизвестно"


def _mapping(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _version(value: Any) -> str:
    return value if isinstance(value, str) and _VERSION.fullmatch(value) else "unknown"


def _event(value: Any) -> str:
    event = _mapping(value)
    if not event:
        return "не зафиксирован"
    operation = _choice(event.get("operation"), OPERATIONS, "other")
    method = _choice(event.get("method"), {"GET", "POST", "PATCH", "DELETE", "LOCAL"})
    status = _number(event.get("status"), 599)
    code = _choice(event.get("code"), ERROR_CODES, "request_failed")
    field = _choice(event.get("field"), FIELDS)
    rule = _choice(event.get("rule"), RULES)
    reference = event.get("reference")
    reference = reference if isinstance(reference, str) and _REFERENCE.fullmatch(reference) else "—"
    at_ms = _number(event.get("at_ms"), 4_102_444_800_000)
    at = datetime.fromtimestamp(at_ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC") if at_ms else "время неизвестно"
    elapsed = _number(event.get("elapsed_ms"), 3_600_000)
    return f"{at} · {operation} {method} · HTTP {status} · {code} · поле={field} · правило={rule} · {elapsed} мс · ref={reference}"


def _environment(user_agent: str) -> str:
    # Only numeric version groups and fixed names can leave this function.
    ua = user_agent[:1024]
    os_name = "unknown"
    for pattern, name in (
        (r"Android[ /]([0-9.]{1,20})", "Android"),
        (r"(?:iPhone OS|CPU OS) ([0-9_]{1,20})", "iOS"),
        (r"Windows NT ([0-9.]{1,20})", "Windows NT"),
        (r"Mac OS X ([0-9_]{1,20})", "macOS"),
    ):
        match = re.search(pattern, ua)
        if match:
            os_name = name + " " + _version(match[1].replace("_", "."))
            break
    if os_name == "unknown" and "Linux" in ua:
        os_name = "Linux"
    browser = "unknown"
    for pattern, name in (
        (r"(?:Edg|EdgA|EdgiOS)/([0-9.]{1,20})", "Edge"),
        (r"(?:Chrome|CriOS)/([0-9.]{1,20})", "Chrome"),
        (r"(?:Firefox|FxiOS)/([0-9.]{1,20})", "Firefox"),
        (r"Version/([0-9.]{1,20}).*Safari/", "Safari"),
    ):
        match = re.search(pattern, ua)
        if match:
            browser = name + " " + _version(match[1])
            break
    return f"ОС: {os_name}; браузер/WebView: {browser}"


def support_message(*, user_id: int, username: str | None, category: str,
                    platform: Any, diagnostics: Any, user_agent: str,
                    reference: str, now: int) -> str:
    d = _mapping(diagnostics)
    contact = f"@{username}" if isinstance(username, str) and re.fullmatch(r"[A-Za-z0-9_]{1,32}", username) else f"tg://user?id={user_id}"
    lines = [
        "🛟 Mini App: обращение в техподдержку",
        f"Обращение: {reference}",
        datetime.fromtimestamp(now, timezone.utc).strftime("Получено: %Y-%m-%d %H:%M:%S UTC"),
        f"Telegram ID: {user_id}", f"Контакт: {contact}",
        f"Платформа: {_choice(platform, PLATFORMS)}; Telegram WebApp API: {_version(d.get('telegram_version'))}",
        _environment(user_agent), f"Проблема: {category}",
        "Клиентская диагностика (со слов приложения):",
        f"Сборка: {'support-v1' if d.get('build') == 'support-v1' else 'legacy/unknown'}; сеть онлайн: {_flag(d.get('online'))}; сессия запущена: {_flag(d.get('bootstrapped'))}",
    ]
    try:
        submission_id = str(UUID(d.get("submission_id")))
    except (ValueError, TypeError, AttributeError):
        submission_id = "—"
    lines.extend([
        f"Черновик: {submission_id}; v{_number(d.get('submission_version'), 2_147_483_647)}; статус={_choice(d.get('submission_status'), STATUSES)}",
        f"Есть несохраненные изменения: {_flag(d.get('dirty'))}; требуется обновление: {_flag(d.get('reload_required'))}",
        f"Вложения: на сервере={_number(d.get('attachment_count'), 3)}, на устройстве={_number(d.get('pending_file_count'), 3)}",
        f"В процессе: сохранение={_flag(d.get('saving'))}, загрузка={_flag(d.get('uploading'))}, отправка={_flag(d.get('submitting'))}",
        "Последний сбой: " + _event(d.get("last_failure")),
    ])
    events = d.get("recent_requests")
    if isinstance(events, list) and events:
        lines.append("Последние запросы (HTTP 0 = ответа нет):")
        lines.extend(_event(event) for event in events[-8:])
    lines.extend([
        f"Проверка заявок: /writers_audit {user_id}",
        "Диагностика: без текста анкеты, файлов, cookies и токенов.",
    ])
    return "\n".join(lines)

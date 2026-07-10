"""Canonical chat/topic scope helpers for Lexicon rounds.

Telegram may expose the general discussion scope as either no thread ID or the
service thread ID 1. Topicless chats must always have one Lexicon round per chat,
while real forum topics must remain independent.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

GENERAL_TOPIC_THREAD_IDS = frozenset({0, 1})


def normalize_round_thread_id(
    chat_id: int,
    message_thread_id: int | None,
    *,
    is_forum: bool | None = None,
    single_scope_chat_ids: Iterable[int] = (),
) -> int | None:
    """Return a stable thread ID for storing and finding a Lexicon round."""
    normalized_chat_id = int(chat_id)
    single_scope_ids = {int(value) for value in single_scope_chat_ids}

    if normalized_chat_id in single_scope_ids:
        return None
    if is_forum is False:
        return None
    if message_thread_id is None:
        return None

    normalized_thread_id = int(message_thread_id)
    if normalized_thread_id in GENERAL_TOPIC_THREAD_IDS:
        return None
    return normalized_thread_id


def normalized_round_key(
    chat_id: int,
    message_thread_id: int | None,
    *,
    single_scope_chat_ids: Iterable[int] = (),
) -> tuple[int, int | None]:
    """Normalize a key when only chat ID and a previously stored thread are known."""
    normalized_chat_id = int(chat_id)
    return (
        normalized_chat_id,
        normalize_round_thread_id(
            normalized_chat_id,
            message_thread_id,
            single_scope_chat_ids=single_scope_chat_ids,
        ),
    )


def round_key_from_message(
    message: Any,
    *,
    single_scope_chat_ids: Iterable[int] = (),
) -> tuple[int, int | None]:
    """Build the canonical round key from an aiogram Message-like object."""
    chat_id = int(message.chat.id)
    is_forum = getattr(message.chat, "is_forum", None)
    thread_id = normalize_round_thread_id(
        chat_id,
        getattr(message, "message_thread_id", None),
        is_forum=is_forum,
        single_scope_chat_ids=single_scope_chat_ids,
    )
    return chat_id, thread_id


def russian_word_count_form(count: int) -> str:
    """Return слово/слова/слов for an integer count."""
    value = abs(int(count))
    last_two = value % 100
    if 11 <= last_two <= 14:
        return "слов"
    last = value % 10
    if last == 1:
        return "слово"
    if 2 <= last <= 4:
        return "слова"
    return "слов"


__all__ = [
    "GENERAL_TOPIC_THREAD_IDS",
    "normalize_round_thread_id",
    "normalized_round_key",
    "round_key_from_message",
    "russian_word_count_form",
]

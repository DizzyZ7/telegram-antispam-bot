"""Isolated chat scope for Lexicon, with delete-only language protection.

Chats listed here are deliberately not added to legacy_main.ALLOWED_CHATS;
only Lexicon and a narrowly scoped profanity-deletion middleware can handle them.
Summaries, legacy moderation, stats, captcha and unrelated commands stay disabled.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.filters import BaseFilter
from aiogram.types import Message

from writers_moderation import contains_prohibited_language

LOGGER = logging.getLogger(__name__)
LEXICON_ONLY_CHAT_IDS: frozenset[int] = frozenset({-1002659916114})

# Harmless fairy joke only, NOT a monetary/account penalty. One notice per
# chat cooldown avoids making the bot an active participant in conversation.
LEXICON_FAIRY_NOTICE_COOLDOWN_SECONDS = 90
LEXICON_FAIRY_NOTICE = (
    "🧚 Ой-ой! Это чат фей, вольных художников, сказочниц, поэтов "
    "и просто классных ребят! 🌺\n\n"
    "Не будем пугать тонкие творческие натуры хардкором припортовой "
    "инфраструктуры — мат уже испарился. 🪄\n\n"
    "А то феи наколдуют штраф — по 50 кроко каждой! 🐊✨"
)


def enforce_lexicon_only_isolation(base_app: Any, chat_ids: Iterable[int]) -> frozenset[int]:
    """Remove Lexicon-only chats from the global legacy allowlist.

    This keeps isolation intact even when an old BotHost ALLOWED_CHATS environment
    variable accidentally contains one of these chat IDs.
    """
    normalized_ids = frozenset(int(chat_id) for chat_id in chat_ids)
    allowed_chats = getattr(base_app, "ALLOWED_CHATS", None)
    if isinstance(allowed_chats, list):
        base_app.ALLOWED_CHATS[:] = [
            chat_id
            for chat_id in allowed_chats
            if int(chat_id) not in normalized_ids
        ]
    return normalized_ids


class SilentLexiconProfanityMiddleware(BaseMiddleware):
    """Delete Lexicon profanity; post a throttled fairy notice only on success.

    Runs before game guessing. No legacy moderation, captcha, or chatting.
    """

    def __init__(self, chat_ids: Iterable[int]) -> None:
        self.chat_ids = frozenset(int(chat_id) for chat_id in chat_ids)
        self.last_fairy_notice: dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        if int(event.chat.id) not in self.chat_ids:
            return await handler(event, data)

        text = event.text or event.caption or ""
        user = getattr(event, "from_user", None)
        anonymous_admin = getattr(event, "sender_chat", None) is not None
        human = user is not None and (
            not bool(getattr(user, "is_bot", False)) or anonymous_admin
        )
        if not human or not text or not contains_prohibited_language(text):
            return await handler(event, data)

        try:
            await event.delete()
        except Exception as exc:
            LOGGER.warning(
                "LEXICON_PROFANITY_DELETE_FAILED chat_id=%s message_id=%s "
                "reason=%s",
                event.chat.id, getattr(event, "message_id", None),
                type(exc).__name__,
            )
        else:
            LOGGER.info(
                "LEXICON_PROFANITY_DELETED chat_id=%s message_id=%s",
                event.chat.id, getattr(event, "message_id", None),
            )
            now = time.monotonic()
            previous = self.last_fairy_notice.get(int(event.chat.id))
            if (
                previous is None
                or now - previous >= LEXICON_FAIRY_NOTICE_COOLDOWN_SECONDS
            ):
                try:
                    await event.answer(LEXICON_FAIRY_NOTICE)
                except Exception as exc:
                    LOGGER.warning(
                        "LEXICON_FAIRY_NOTICE_SEND_FAILED chat_id=%s reason=%s",
                        event.chat.id, type(exc).__name__,
                    )
                else:
                    self.last_fairy_notice[int(event.chat.id)] = now
        # Never advance the game with profanity, even when deletion fails.
        return None


def register_lexicon_silent_profanity_filter(
    app: Any, chat_ids: Iterable[int],
) -> None:
    """Register before the game's outer middleware, without widening access."""
    normalized = frozenset(int(chat_id) for chat_id in chat_ids)
    if not normalized:
        return
    app.dp.message.outer_middleware(SilentLexiconProfanityMiddleware(normalized))
    print(
        "LEXICON_PROFANITY_FAIRY_NOTICE_READY "
        f"chat_ids={','.join(str(value) for value in sorted(normalized))} "
        f"cooldown_seconds={LEXICON_FAIRY_NOTICE_COOLDOWN_SECONDS} "
        "legacy_warnings=off other_features=blocked",
        flush=True,
    )


async def report_lexicon_delete_permission(bot: Any, chat_ids: Iterable[int]) -> None:
    """Best-effort diagnostics; cannot silently assume Delete permission."""
    for chat_id in sorted(frozenset(int(chat_id) for chat_id in chat_ids)):
        try:
            member = await bot.get_chat_member(chat_id=chat_id, user_id=int(bot.id))
        except Exception as exc:
            LOGGER.warning(
                "LEXICON_PROFANITY_PERMISSION_CHECK_FAILED chat_id=%s reason=%s",
                chat_id, type(exc).__name__,
            )
            continue
        raw_status = getattr(member, "status", "")
        status = str(getattr(raw_status, "value", raw_status)).lower()
        permitted = status in {"creator", "owner"} or (
            status == "administrator"
            and bool(getattr(member, "can_delete_messages", False))
        )
        if permitted:
            LOGGER.info(
                "LEXICON_PROFANITY_DELETE_PERMISSION_READY chat_id=%s", chat_id
            )
        else:
            LOGGER.error(
                "LEXICON_PROFANITY_DELETE_PERMISSION_MISSING chat_id=%s "
                "bot_status=%s grant administrator Delete messages permission",
                chat_id, status,
            )


class LexiconGameAppScope:
    """Delegate the full app API while extending only Lexicon chat access."""

    __slots__ = ("_base_app", "_lexicon_only_chat_ids")

    def __init__(self, base_app: Any, lexicon_only_chat_ids: Iterable[int]) -> None:
        self._base_app = base_app
        self._lexicon_only_chat_ids = frozenset(int(chat_id) for chat_id in lexicon_only_chat_ids)

    @property
    def lexicon_only_chat_ids(self) -> frozenset[int]:
        return self._lexicon_only_chat_ids

    def is_allowed_chat(self, chat_id: int) -> bool:
        return bool(
            self._base_app.is_allowed_chat(chat_id)
            or int(chat_id) in self._lexicon_only_chat_ids
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._base_app, name)


class LexiconOnlyChatFilter(BaseFilter):
    """Match messages from chats that must expose only the Lexicon game."""

    __slots__ = ("chat_ids",)

    def __init__(self, chat_ids: Iterable[int]) -> None:
        self.chat_ids = frozenset(int(chat_id) for chat_id in chat_ids)

    async def __call__(self, message: Message) -> bool:
        return int(message.chat.id) in self.chat_ids


def register_lexicon_only_guard(app: Any, chat_ids: Iterable[int]) -> None:
    """Consume Lexicon-only messages before legacy handlers receive them.

    Register this guard before register_minigame_handlers(). The game command
    handlers are then promoted above it, while ordinary guesses are processed by
    the Lexicon outer middleware before the guard consumes the message.
    """
    normalized_ids = frozenset(int(chat_id) for chat_id in chat_ids)
    if not normalized_ids:
        return

    dispatcher = app.dp

    @dispatcher.message(LexiconOnlyChatFilter(normalized_ids))
    async def lexicon_only_chat_guard(message: Message) -> None:
        LOGGER.debug(
            "Lexicon-only guard consumed message chat_id=%s message_id=%s",
            message.chat.id,
            message.message_id,
        )

    dispatcher.message.handlers.insert(0, dispatcher.message.handlers.pop())
    print(
        "LEXICON_ONLY_GUARD_READY "
        f"chat_ids={','.join(str(chat_id) for chat_id in sorted(normalized_ids))} "
        "legacy_handlers=blocked",
        flush=True,
    )


__all__ = [
    "LEXICON_ONLY_CHAT_IDS",
    "LexiconGameAppScope",
    "LexiconOnlyChatFilter",
    "SilentLexiconProfanityMiddleware",
    "LEXICON_FAIRY_NOTICE",
    "LEXICON_FAIRY_NOTICE_COOLDOWN_SECONDS",
    "register_lexicon_silent_profanity_filter",
    "report_lexicon_delete_permission",
    "enforce_lexicon_only_isolation",
    "register_lexicon_only_guard",
]

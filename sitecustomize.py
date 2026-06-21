"""Runtime additions for the writers chat without changing behavior in other chats."""

from __future__ import annotations

import base64
import logging
import os
import re
import sys
import time
import unicodedata
from functools import wraps

from aiogram import Dispatcher, F
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import ChatMemberUpdatedFilter, IS_MEMBER, IS_NOT_MEMBER
from aiogram.types import ChatMemberUpdated, ChatPermissions, Message

LOGGER = logging.getLogger("writers_chat_moderation")
WRITERS_CHAT_USERNAME = os.getenv("WRITERS_CHAT_USERNAME", "chat_ikf").lstrip("@").lower()
WRITERS_RULES_URL = os.getenv(
    "WRITERS_RULES_URL",
    "https://t.me/" + "chat_IKF/168194/168197",
)

LOOKALIKE_MAP = str.maketrans(
    {
        "a": "а", "c": "с", "e": "е", "k": "к", "m": "м", "o": "о",
        "p": "р", "t": "т", "x": "х", "y": "у", "0": "о", "1": "и",
        "2": "з", "3": "з", "4": "а", "5": "с", "6": "б", "7": "т",
        "8": "в", "9": "я", "@": "а", "$": "с",
    }
)


def _decode(encoded: str) -> str:
    return base64.b64decode(encoded).decode("utf-8")


CYRILLIC_EXACT_TOKENS = frozenset(
    _decode(value)
    for value in (
        "0LHQu9GP", "0LHQu9GP0YLRjA==", "0LHQu9GP0YI=",
    )
)

CYRILLIC_PREFIX_TOKENS = tuple(
    _decode(value)
    for value in (
        "0LHQu9GP0LQ=", "0LXQsQ==", "0L/QuNC30LQ=", "0YXRg9C5",
        "0LfQsNC70YPQvw==", "0LXQsdC70LDQvQ==", "0YPQtdCx",
        "0LTQvtC70LHQvtC10LE=", "0LPQsNC90LTQvtC9", "0L/QuNC00L7RgA==",
        "0L/QuNC00YA=", "0L/QtdC00LjQug==", "0LzRg9C00LDQug==",
        "0LzRg9C00LjQu9Cw",
    )
)

LATIN_EXACT_TOKENS = frozenset(
    _decode(value)
    for value in (
        "ZnVjaw==", "ZmNr", "c2hpdA==", "Yml0Y2g=", "YXNzaG9sZQ==", "ZGljaw==",
        "Ymx5YXQ=", "Ymx5YWQ=", "cGl6ZGE=", "cGl6ZGVj", "cGl6ZGV0cw==",
        "ZWJhdA==", "eWViYXQ=", "aHV5", "aHVp", "aHVpbnlh",
    )
)

LATIN_PREFIX_TOKENS = tuple(
    _decode(value)
    for value in (
        "Ymx5YWQ=", "cGl6ZA==",
    )
)

WARNING_COOLDOWN_SECONDS = 60
warning_timestamps: dict[tuple[int, int], float] = {}


class WritersChatScope:
    def __init__(self) -> None:
        configured_id = os.getenv("WRITERS_CHAT_ID", "").strip()
        self.chat_id = int(configured_id) if configured_id.lstrip("-").isdigit() else None

    async def resolve(self, bot) -> None:
        if self.chat_id is not None:
            return
        try:
            chat = await bot.get_chat("@" + WRITERS_CHAT_USERNAME)
        except Exception as exc:
            LOGGER.warning("Could not resolve writers chat id by username: %s", exc)
            return
        self.chat_id = chat.id
        LOGGER.info("Writers chat resolved: chat_id=%s", self.chat_id)

    def matches(self, chat) -> bool:
        if self.chat_id is not None:
            return chat.id == self.chat_id
        return (chat.username or "").lower() == WRITERS_CHAT_USERNAME


def _normalize_token(token: str) -> str:
    token = unicodedata.normalize("NFKC", token).casefold().replace("е", "е")
    token = token.translate(LOOKALIKE_MAP)
    token = re.sub(r"[^а-яa-z0-9]", "", token)
    return re.sub(r"(.)\1{2,}", r"\1", token)


def _latin_token(token: str) -> str:
    token = unicodedata.normalize("NFKC", token).casefold()
    token = re.sub(r"[^a-z0-9]", "", token)
    return re.sub(r"(.)\1{2,}", r"\1", token)


def contains_banned_language(text: str) -> bool:
    for raw_token in re.findall(r"[а-яa-z0-9@#$]+", text, flags=re.IGNORECASE):
        latin_token = _latin_token(raw_token)
        if latin_token in LATIN_EXACT_TOKENS:
            return True
        if any(latin_token.startswith(prefix) for prefix in LATIN_PREFIX_TOKENS):
            return True

        token = _normalize_token(raw_token)
        if token in CYRILLIC_EXACT_TOKENS:
            return True
        if any(token.startswith(prefix) for prefix in CYRILLIC_PREFIX_TOKENS):
            return True
    return False


def build_welcome_text(name: str, question: str) -> str:
    return "\n".join(
        (
            f"✒️ <b>{name}</b>, добро пожаловать в пространство авторов и читателей.",
            "",
            "Здесь обсуждают истории, персонажей, идеи и тексты. Давайте сохранять атмосферу, в которой приятно и писать, и читать.",
            "",
            f"📖 <a href=\"{WRITERS_RULES_URL}\">Правила чата</a>",
            "",
            "Пройди короткую проверку и присоединяйся:",
            "",
            f"<b>{question}</b>",
        )
    )


def build_warning_text() -> str:
    return "\n".join(
        (
            "⚠️ <b>Сообщение удалено</b>",
            "",
            "В этом чате общаются авторы, читатели и люди, которым важны истории. Мат здесь не используем — давай оставим разговор комфортным и понятным для всех.",
            "",
            f"📖 <a href=\"{WRITERS_RULES_URL}\">Правила чата</a>",
            "(°-°)",
        )
    )


def _register_first(observer, handler, *filters) -> None:
    observer.register(handler, *filters)
    observer.handlers.insert(0, observer.handlers.pop())


def _install(dispatcher: Dispatcher, module, scope: WritersChatScope) -> None:
    async def on_writers_chat_join(event: ChatMemberUpdated) -> None:
        if not scope.matches(event.chat):
            raise SkipHandler
        if not module.is_allowed_chat(event.chat.id):
            raise SkipHandler

        user = event.new_chat_member.user
        if user.id in module.passed_users:
            return

        question, answer, keyboard = module.build_captcha(user.id)
        module.pending_users[user.id] = answer
        await module.bot.restrict_chat_member(
            event.chat.id,
            user.id,
            ChatPermissions(can_send_messages=False),
        )
        name = module.safe_output_text(user.full_name or str(user.id))
        await module.bot.send_message(
            event.chat.id,
            build_welcome_text(name, question),
            reply_markup=keyboard,
        )

    async def moderate_writers_chat_message(message: Message) -> None:
        if not scope.matches(message.chat):
            raise SkipHandler
        if not module.is_allowed_chat(message.chat.id):
            raise SkipHandler
        if message.from_user is None or message.from_user.is_bot:
            raise SkipHandler
        if not message.text or message.text.startswith("/"):
            raise SkipHandler
        if not contains_banned_language(message.text):
            raise SkipHandler

        try:
            await message.delete()
        except Exception as exc:
            LOGGER.warning("Could not delete moderated message: %s", exc)
            return

        key = (message.chat.id, message.from_user.id)
        now = time.monotonic()
        previous = warning_timestamps.get(key, 0.0)
        if now - previous < WARNING_COOLDOWN_SECONDS:
            return
        warning_timestamps[key] = now

        params = {"chat_id": message.chat.id, "text": build_warning_text()}
        if message.message_thread_id:
            params["message_thread_id"] = message.message_thread_id
        await module.bot.send_message(**params)

    _register_first(
        dispatcher.chat_member,
        on_writers_chat_join,
        ChatMemberUpdatedFilter(IS_NOT_MEMBER >> IS_MEMBER),
    )
    _register_first(dispatcher.message, moderate_writers_chat_message, F.text)


_original_start_polling = Dispatcher.start_polling


@wraps(_original_start_polling)
async def _patched_start_polling(self: Dispatcher, *bots, **kwargs):
    if not getattr(self, "_writers_chat_moderation_installed", False):
        module = sys.modules.get("__main__")
        required = ("bot", "is_allowed_chat", "build_captcha", "safe_output_text", "pending_users", "passed_users")
        if module is not None and all(hasattr(module, name) for name in required):
            scope = WritersChatScope()
            await scope.resolve(module.bot)
            _install(self, module, scope)
            self._writers_chat_moderation_installed = True
    return await _original_start_polling(self, *bots, **kwargs)


Dispatcher.start_polling = _patched_start_polling

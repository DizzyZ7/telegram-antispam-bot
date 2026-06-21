"""Scoped moderation for the writers and readers forum chat."""

from __future__ import annotations

import logging
import os
import re
import time
import unicodedata
from typing import Any

from aiogram.filters import BaseFilter, ChatMemberUpdatedFilter, IS_MEMBER, IS_NOT_MEMBER
from aiogram.types import Chat, ChatMemberUpdated, ChatPermissions, Message

LOGGER = logging.getLogger(__name__)
WRITERS_CHAT_USERNAME = os.getenv("WRITERS_CHAT_USERNAME", "chat_ikf").lstrip("@").lower()
WRITERS_RULES_URL = os.getenv(
    "WRITERS_RULES_URL",
    "https://t.me/" + "chat_IKF/168194/168197",
)
WARNING_COOLDOWN_SECONDS = max(10, int(os.getenv("WRITERS_WARNING_COOLDOWN_SECONDS", "60")))

LOOKALIKE_MAP = str.maketrans(
    {
        "a": "а", "b": "б", "c": "с", "d": "д", "e": "е", "g": "г",
        "h": "н", "i": "и", "k": "к", "l": "л", "m": "м", "o": "о",
        "p": "р", "t": "т", "v": "в", "x": "х", "y": "у", "z": "з",
        "0": "о", "1": "и", "2": "з", "3": "з", "4": "а", "5": "с",
        "6": "б", "7": "т", "8": "в", "9": "я", "@": "а", "$": "с",
    }
)

CYRILLIC_PATTERNS = (
    re.compile(r"^бля+$"),
    re.compile(r"^бляд[а-я]*$"),
    re.compile(r"^блят[а-я]*$"),
    re.compile(r"^(?:еб|заеб|выеб|доеб|наеб|поеб)[а-я]*$"),
    re.compile(r"^(?:пизд|пезд)[а-я]*$"),
    re.compile(r"^(?:хуй|хуе|хуи|оху|наху|поху)[а-я]*$"),
    re.compile(r"^(?:долбоеб|гандон)[а-я]*$"),
    re.compile(r"^мудак(?:и|а|у|ом|е|ов|ами)?$"),
    re.compile(r"^сука$"),
    re.compile(r"^(?:пидор|пидарас|пидорас|пидр|педик)$"),
)
LATIN_PATTERNS = (
    re.compile(r"^(?:fuck|fck|shit|bitch|asshole|dick)[a-z]*$"),
    re.compile(r"^(?:blya|blyad|blyat|pizd[a-z]*|ebat|yebat|huy|hui|huinya|suka|mudak|gandon|pidor|pidar)$"),
)


class WritersChatScope:
    def __init__(self) -> None:
        configured_id = os.getenv("WRITERS_CHAT_ID", "").strip()
        self.chat_id = int(configured_id) if configured_id.lstrip("-").isdigit() else None

    async def resolve(self, bot: Any) -> None:
        if self.chat_id is not None:
            return
        try:
            chat = await bot.get_chat("@" + WRITERS_CHAT_USERNAME)
        except Exception as exc:
            LOGGER.warning("Could not resolve writers chat id: %s", exc)
            return
        self.chat_id = chat.id
        LOGGER.info("Writers chat id resolved: %s", self.chat_id)

    def matches(self, chat: Chat) -> bool:
        if self.chat_id is not None:
            return chat.id == self.chat_id
        return (chat.username or "").lower() == WRITERS_CHAT_USERNAME


def _collapse_repeats(value: str) -> str:
    return re.sub(r"(.)\1{2,}", r"\1", value)


def _normalize_mixed_token(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")
    value = value.translate(LOOKALIKE_MAP)
    value = re.sub(r"[^a-zа-я0-9]", "", value)
    return _collapse_repeats(value)


def _normalize_latin_token(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    value = re.sub(r"[^a-z0-9]", "", value)
    return _collapse_repeats(value)


def contains_prohibited_language(text: str) -> bool:
    for raw_token in re.findall(r"[A-Za-zА-Яа-я0-9@#$]+", text):
        latin_token = _normalize_latin_token(raw_token)
        if any(pattern.fullmatch(latin_token) for pattern in LATIN_PATTERNS):
            return True

        token = _normalize_mixed_token(raw_token)
        if any(pattern.fullmatch(token) for pattern in CYRILLIC_PATTERNS):
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


class WritersChatFilter(BaseFilter):
    def __init__(self, scope: WritersChatScope, allowed_chats: list[int]) -> None:
        self.scope = scope
        self.allowed_chats = allowed_chats

    async def __call__(self, event: Message | ChatMemberUpdated) -> bool:
        if not self.scope.matches(event.chat):
            return False
        if self.scope.chat_id is None:
            self.scope.chat_id = event.chat.id
            LOGGER.info("Writers chat id learned from update: %s", self.scope.chat_id)
        if event.chat.id not in self.allowed_chats:
            self.allowed_chats.append(event.chat.id)
        return True


class ProhibitedLanguageFilter(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        return bool(
            message.from_user
            and not message.from_user.is_bot
            and message.text
            and not message.text.startswith("/")
            and contains_prohibited_language(message.text)
        )


def register_writers_chat_handlers(module: Any) -> WritersChatScope:
    scope = WritersChatScope()
    chat_filter = WritersChatFilter(scope, module.ALLOWED_CHATS)
    last_warning_at: dict[tuple[int, int], float] = {}

    @module.dp.chat_member(
        chat_filter,
        ChatMemberUpdatedFilter(IS_NOT_MEMBER >> IS_MEMBER),
    )
    async def on_writers_chat_join(event: ChatMemberUpdated) -> None:
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

    @module.dp.message(chat_filter, ProhibitedLanguageFilter())
    async def remove_prohibited_language(message: Message) -> None:
        try:
            await message.delete()
        except Exception as exc:
            LOGGER.warning("Could not delete prohibited message: %s", exc)
            return

        key = (message.chat.id, message.from_user.id)
        now = time.monotonic()
        if now - last_warning_at.get(key, 0.0) < WARNING_COOLDOWN_SECONDS:
            return
        last_warning_at[key] = now

        params: dict[str, Any] = {
            "chat_id": message.chat.id,
            "text": build_warning_text(),
        }
        if message.message_thread_id is not None:
            params["message_thread_id"] = message.message_thread_id

        try:
            await module.bot.send_message(**params)
        except Exception as exc:
            LOGGER.warning("Could not send moderation warning: %s", exc)

    module.dp.chat_member.handlers.insert(0, module.dp.chat_member.handlers.pop())
    module.dp.message.handlers.insert(0, module.dp.message.handlers.pop())
    return scope

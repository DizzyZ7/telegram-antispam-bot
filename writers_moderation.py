"""Scoped moderation for the writers and readers forum chat."""

from __future__ import annotations

import logging
import os
import re
import time
import unicodedata
from typing import Any

from aiogram import F
from aiogram.filters import BaseFilter, ChatMemberUpdatedFilter, IS_MEMBER, IS_NOT_MEMBER
from aiogram.types import (
    CallbackQuery,
    Chat,
    ChatMemberUpdated,
    ChatPermissions,
    LinkPreviewOptions,
    Message,
)

LOGGER = logging.getLogger(__name__)
WRITERS_CHAT_USERNAME = os.getenv("WRITERS_CHAT_USERNAME", "chat_ikf").lstrip("@").lower()
WRITERS_RULES_URL = os.getenv(
    "WRITERS_RULES_URL",
    "https://t.me/" + "chat_IKF/168194/168197",
)
WARNING_COOLDOWN_SECONDS = max(10, int(os.getenv("WRITERS_WARNING_COOLDOWN_SECONDS", "60")))
RULES_LINK_PREVIEW_OPTIONS = LinkPreviewOptions(is_disabled=True)
EXTRA_BLOCKED_TERMS_RAW = tuple(
    item.strip()
    for item in re.split(r"[,;\n]", os.getenv("WRITERS_EXTRA_BLOCKED_TERMS", ""))
    if item.strip()
)

LOOKALIKE_MAP = str.maketrans(
    {
        "a": "а", "b": "б", "c": "с", "d": "д", "e": "е", "g": "г",
        "h": "н", "i": "и", "k": "к", "l": "л", "m": "м", "o": "о",
        "p": "р", "t": "т", "v": "в", "x": "х", "y": "у", "z": "з",
        "0": "о", "1": "и", "2": "з", "3": "з", "4": "а", "5": "с",
        "6": "б", "7": "т", "8": "в", "9": "я", "@": "а", "$": "с",
    }
)

WORD_TOKEN_PATTERN = re.compile(r"[A-Za-zА-Яа-я0-9@#$]+")
SPACED_TOKEN_PATTERN = re.compile(
    r"(?<![A-Za-zА-Яа-я0-9@#$])"
    r"(?:[A-Za-zА-Яа-я0-9@#$][\s._*|!/\\\-\u200b]+){2,}"
    r"[A-Za-zА-Яа-я0-9@#$]"
    r"(?![A-Za-zА-Яа-я0-9@#$])"
)

OBSCENE_CYRILLIC_PATTERNS = (
    re.compile(r"^бля+$"),
    re.compile(r"^бляд[а-я]*$"),
    re.compile(r"^блят[а-я]*$"),
    re.compile(r"^(?:еб|заеб|выеб|доеб|наеб|поеб|уеб|разеб|разъеб)[а-я]*$"),
    re.compile(r"^(?:пизд|пезд)[а-я]*$"),
    re.compile(r"^(?:хуй|хуе|хуи|хуя|оху|наху|поху)[а-я]*$"),
    re.compile(r"^(?:залуп|хуесос|пиздобол|пиздюк|пиздюл|пиздот|пиздат)[а-я]*$"),
    re.compile(r"^(?:долбоеб|гандон|мудак|мудач|мудил)[а-я]*$"),
    re.compile(r"^сука$"),
    re.compile(r"^пид(?:ор|орас|арас)[а-я]*$"),
    re.compile(r"^педик(?:а|у|ом|е|и|ов|ам|ами)?$"),
)

TOXIC_INSULT_CYRILLIC_PATTERNS = (
    re.compile(r"^дебил[а-я]*$"),
    re.compile(r"^кретин[а-я]*$"),
    re.compile(r"^имбецил[а-я]*$"),
    re.compile(r"^дегенерат[а-я]*$"),
    re.compile(r"^ублюд[а-я]*$"),
    re.compile(r"^мраз[а-я]*$"),
    re.compile(r"^сволоч[а-я]*$"),
    re.compile(r"^гнид[а-я]*$"),
    re.compile(r"^чмо(?:шник|шница|шка)?[а-я]*$"),
    re.compile(r"^тупиц[а-я]*$"),
    re.compile(r"^недоумок[а-я]*$"),
)

OBSCENE_LATIN_PATTERNS = (
    re.compile(r"^(?:fuck|fck|shit|bitch|asshole|dick)[a-z]*$"),
    re.compile(r"^(?:blya|blyad|blyat|pizd[a-z]*|ebat|yebat|huy|hui|huinya)$"),
    re.compile(r"^(?:suka|mudak|gandon|dolboeb)[a-z]*$"),
    re.compile(r"^pid(?:or|ar|oras|aras|as)[a-z]*$"),
    re.compile(r"^pedor[a-z]*$"),
)

TOXIC_INSULT_LATIN_PATTERNS = (
    re.compile(r"^debil[a-z]*$"),
    re.compile(r"^cretin[a-z]*$"),
    re.compile(r"^imbecil[a-z]*$"),
    re.compile(r"^degenerat[a-z]*$"),
    re.compile(r"^ublyud[a-z]*$"),
    re.compile(r"^mraz[a-z]*$"),
    re.compile(r"^svoloch[a-z]*$"),
    re.compile(r"^gnida[a-z]*$"),
    re.compile(r"^chmo[a-z]*$"),
    re.compile(r"^tupica[a-z]*$"),
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
    return re.sub(r"(.)\1+", r"\1", value)


def _normalize_mixed_token(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")
    value = value.translate(LOOKALIKE_MAP)
    value = re.sub(r"[^a-zа-я0-9]", "", value)
    return _collapse_repeats(value)


def _normalize_latin_token(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    value = re.sub(r"[^a-z0-9]", "", value)
    return _collapse_repeats(value)


def _build_extra_blocked_tokens() -> frozenset[str]:
    normalized_terms: set[str] = set()
    for raw_term in EXTRA_BLOCKED_TERMS_RAW:
        mixed_term = _normalize_mixed_token(raw_term)
        latin_term = _normalize_latin_token(raw_term)
        if mixed_term:
            normalized_terms.add(mixed_term)
        if latin_term:
            normalized_terms.add(latin_term)
    return frozenset(normalized_terms)


EXTRA_BLOCKED_TOKENS = _build_extra_blocked_tokens()


def _matches_any(value: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    return any(pattern.fullmatch(value) for pattern in patterns)


def _is_prohibited_token(raw_token: str) -> bool:
    latin_token = _normalize_latin_token(raw_token)
    if latin_token in EXTRA_BLOCKED_TOKENS:
        return True
    if _matches_any(latin_token, OBSCENE_LATIN_PATTERNS):
        return True
    if _matches_any(latin_token, TOXIC_INSULT_LATIN_PATTERNS):
        return True

    token = _normalize_mixed_token(raw_token)
    if token in EXTRA_BLOCKED_TOKENS:
        return True
    if _matches_any(token, OBSCENE_CYRILLIC_PATTERNS):
        return True
    return _matches_any(token, TOXIC_INSULT_CYRILLIC_PATTERNS)


def contains_prohibited_language(text: str) -> bool:
    for raw_token in WORD_TOKEN_PATTERN.findall(text):
        if _is_prohibited_token(raw_token):
            return True

    for match in SPACED_TOKEN_PATTERN.finditer(text):
        compact_token = re.sub(r"[^A-Za-zА-Яа-я0-9@#$]", "", match.group())
        if _is_prohibited_token(compact_token):
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


def build_captcha_success_text(user_tag: str) -> str:
    return "\n".join(
        (
            f"✅ <b>{user_tag}, проверка пройдена.</b>",
            "",
            "Добро пожаловать в беседу авторов и читателей.",
            "",
            f"📖 <a href=\"{WRITERS_RULES_URL}\">Правила чата</a>",
            "",
            "Пожалуйста, ознакомься с ними перед первым сообщением. Приятного общения и вдохновения ✒️",
        )
    )


def build_warning_text() -> str:
    return "\n".join(
        (
            "⚠️ <b>Сообщение удалено</b>",
            "",
            "В этом чате общаются авторы, читатели и люди, которым важны истории. Мат и прямые оскорбления здесь не используем — давай оставим разговор комфортным и понятным для всех.",
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


class WritersCaptchaFilter(BaseFilter):
    def __init__(self, scope: WritersChatScope) -> None:
        self.scope = scope

    async def __call__(self, callback: CallbackQuery) -> bool:
        return bool(callback.message and self.scope.matches(callback.message.chat))


class ProhibitedLanguageFilter(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        content = message.text or message.caption or ""
        return bool(
            message.from_user
            and not message.from_user.is_bot
            and content
            and not content.startswith("/")
            and contains_prohibited_language(content)
        )


def register_writers_chat_handlers(module: Any) -> WritersChatScope:
    scope = WritersChatScope()
    chat_filter = WritersChatFilter(scope, module.ALLOWED_CHATS)
    captcha_filter = WritersCaptchaFilter(scope)
    last_warning_at: dict[tuple[int, int], float] = {}

    @module.dp.chat_member(
        chat_filter,
        ChatMemberUpdatedFilter(IS_NOT_MEMBER >> IS_MEMBER),
    )
    async def on_writers_chat_join(event: ChatMemberUpdated) -> None:
        user = event.new_chat_member.user
        if user.is_bot or user.id in module.passed_users:
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
            link_preview_options=RULES_LINK_PREVIEW_OPTIONS,
        )

    @module.dp.callback_query(F.data.startswith("captcha:"), captcha_filter)
    async def writers_captcha_handler(callback: CallbackQuery) -> None:
        if callback.message is None or callback.data is None:
            return

        try:
            _, target_user_id_raw, value_raw = callback.data.split(":", maxsplit=2)
            target_user_id = int(target_user_id_raw)
            value = int(value_raw)
        except (TypeError, ValueError):
            await callback.answer("Некорректная проверка", show_alert=True)
            return

        if callback.from_user.id != target_user_id:
            await callback.answer("Это не твоя проверка", show_alert=True)
            return

        if target_user_id not in module.pending_users:
            await callback.answer("Проверка уже завершена")
            return

        if value != module.pending_users[target_user_id]:
            module.failed_users.add(target_user_id)
            await callback.answer("❌ Неверно", show_alert=True)
            return

        module.pending_users.pop(target_user_id, None)
        module.passed_users.add(target_user_id)
        module.failed_users.discard(target_user_id)

        chat_id = callback.message.chat.id
        await module.bot.restrict_chat_member(
            chat_id,
            target_user_id,
            ChatPermissions(
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
            ),
        )

        message_thread_id = callback.message.message_thread_id
        try:
            await callback.message.delete()
        except Exception as exc:
            LOGGER.warning("Could not delete completed captcha: %s", exc)

        params: dict[str, Any] = {
            "chat_id": chat_id,
            "text": build_captcha_success_text(module.user_tag(callback.from_user)),
            "link_preview_options": RULES_LINK_PREVIEW_OPTIONS,
        }
        if message_thread_id is not None:
            params["message_thread_id"] = message_thread_id
        await module.bot.send_message(**params)
        await callback.answer("Испытание пройдено")

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
            "link_preview_options": RULES_LINK_PREVIEW_OPTIONS,
        }
        if message.message_thread_id is not None:
            params["message_thread_id"] = message.message_thread_id

        try:
            await module.bot.send_message(**params)
        except Exception as exc:
            LOGGER.warning("Could not send moderation warning: %s", exc)

    module.dp.chat_member.handlers.insert(0, module.dp.chat_member.handlers.pop())
    module.dp.callback_query.handlers.insert(0, module.dp.callback_query.handlers.pop())
    module.dp.message.handlers.insert(0, module.dp.message.handlers.pop())
    return scope

"""Scoped moderation for the writers and readers forum chat."""

from __future__ import annotations

import json
import logging
import os
import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
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
LEXICON_PATH = Path(__file__).resolve().parent / "data" / "moderation_lexicon.json"

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
        "u": "у", "n": "н", "r": "р", "f": "ф", "j": "й",
        "0": "о", "1": "и", "2": "з", "3": "з", "4": "а", "5": "с",
        "6": "б", "7": "т", "8": "в", "9": "я", "@": "а", "$": "с",
    }
)

TOKEN_CHAR_CLASS = r"A-Za-zА-Яа-яЁё0-9@#$"
WORD_TOKEN_PATTERN = re.compile(rf"[{TOKEN_CHAR_CLASS}]+")
SPACED_TOKEN_PATTERN = re.compile(
    rf"(?<![{TOKEN_CHAR_CLASS}])"
    rf"(?:[{TOKEN_CHAR_CLASS}][\s._*|!/\\\-\u200b]+){{1,}}"
    rf"[{TOKEN_CHAR_CLASS}]"
    rf"(?![{TOKEN_CHAR_CLASS}])"
)
SYMBOL_OBFUSCATION_PATTERN = re.compile(
    rf"(?<![{TOKEN_CHAR_CLASS}])"
    rf"[{TOKEN_CHAR_CLASS}](?:[^\w\s]+[{TOKEN_CHAR_CLASS}])+[{TOKEN_CHAR_CLASS}]*"
    rf"(?![{TOKEN_CHAR_CLASS}])",
    re.UNICODE,
)


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


def _compact_candidate(value: str) -> str:
    return re.sub(rf"[^{TOKEN_CHAR_CLASS}]", "", value)


@dataclass(frozen=True, slots=True)
class ModerationLexicon:
    schema_version: int
    allow_mixed: frozenset[str]
    allow_latin: frozenset[str]
    exact_mixed: dict[str, str]
    exact_latin: dict[str, str]
    prefix_mixed: tuple[tuple[str, str], ...]
    prefix_latin: tuple[tuple[str, str], ...]
    rule_count: int


def _empty_lexicon() -> ModerationLexicon:
    return ModerationLexicon(
        schema_version=0,
        allow_mixed=frozenset(),
        allow_latin=frozenset(),
        exact_mixed={},
        exact_latin={},
        prefix_mixed=(),
        prefix_latin=(),
        rule_count=0,
    )


def _load_moderation_lexicon() -> ModerationLexicon:
    try:
        payload = json.loads(LEXICON_PATH.read_text(encoding="utf-8"))
        schema_version = int(payload["schema_version"])
        if schema_version != 1:
            raise ValueError(f"Unsupported moderation lexicon schema: {schema_version}")

        allow_exact = payload.get("allow_exact", [])
        rule_groups = payload.get("rules", {})
        if not isinstance(allow_exact, list) or not isinstance(rule_groups, dict):
            raise ValueError("Moderation lexicon has an invalid shape")

        allow_mixed: set[str] = set()
        allow_latin: set[str] = set()
        for raw_term in allow_exact:
            if not isinstance(raw_term, str):
                continue
            mixed = _normalize_mixed_token(raw_term)
            latin = _normalize_latin_token(raw_term)
            if mixed:
                allow_mixed.add(mixed)
            if latin:
                allow_latin.add(latin)

        exact_mixed: dict[str, str] = {}
        exact_latin: dict[str, str] = {}
        prefix_mixed: list[tuple[str, str]] = []
        prefix_latin: list[tuple[str, str]] = []
        rule_count = 0

        for match_mode, categories in rule_groups.items():
            if match_mode not in {"exact", "prefix"} or not isinstance(categories, dict):
                raise ValueError(f"Unsupported moderation rule group: {match_mode}")

            for category, terms in categories.items():
                if not isinstance(category, str) or not isinstance(terms, list):
                    raise ValueError("Moderation lexicon category is invalid")

                for raw_term in terms:
                    if not isinstance(raw_term, str):
                        continue
                    mixed = _normalize_mixed_token(raw_term)
                    latin = _normalize_latin_token(raw_term)
                    if not mixed and not latin:
                        continue
                    rule_count += 1

                    if match_mode == "exact":
                        if mixed:
                            exact_mixed.setdefault(mixed, category)
                        if latin:
                            exact_latin.setdefault(latin, category)
                    else:
                        if mixed:
                            prefix_mixed.append((mixed, category))
                        if latin:
                            prefix_latin.append((latin, category))

        return ModerationLexicon(
            schema_version=schema_version,
            allow_mixed=frozenset(allow_mixed),
            allow_latin=frozenset(allow_latin),
            exact_mixed=exact_mixed,
            exact_latin=exact_latin,
            prefix_mixed=tuple(sorted(prefix_mixed, key=lambda item: len(item[0]), reverse=True)),
            prefix_latin=tuple(sorted(prefix_latin, key=lambda item: len(item[0]), reverse=True)),
            rule_count=rule_count,
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        LOGGER.error("Could not load moderation lexicon from %s: %s", LEXICON_PATH, exc)
        return _empty_lexicon()


MODERATION_LEXICON = _load_moderation_lexicon()


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


def _match_compiled_rules(
    value: str,
    exact_rules: dict[str, str],
    prefix_rules: tuple[tuple[str, str], ...],
) -> str | None:
    if value in exact_rules:
        return exact_rules[value]
    for prefix, category in prefix_rules:
        if value.startswith(prefix):
            return category
    return None


def _detect_prohibited_token(raw_token: str) -> str | None:
    latin_token = _normalize_latin_token(raw_token)
    if latin_token in EXTRA_BLOCKED_TOKENS:
        return "extra"
    if latin_token not in MODERATION_LEXICON.allow_latin:
        category = _match_compiled_rules(
            latin_token,
            MODERATION_LEXICON.exact_latin,
            MODERATION_LEXICON.prefix_latin,
        )
        if category is not None:
            return category

    token = _normalize_mixed_token(raw_token)
    if token in EXTRA_BLOCKED_TOKENS:
        return "extra"
    if token in MODERATION_LEXICON.allow_mixed:
        return None
    return _match_compiled_rules(
        token,
        MODERATION_LEXICON.exact_mixed,
        MODERATION_LEXICON.prefix_mixed,
    )


def _detect_candidates(matches: list[str] | Any) -> str | None:
    for match in matches:
        compact_token = _compact_candidate(match)
        if not compact_token:
            continue
        category = _detect_prohibited_token(compact_token)
        if category is not None:
            return category
    return None


def detect_prohibited_language(text: str) -> str | None:
    for raw_token in WORD_TOKEN_PATTERN.findall(text):
        category = _detect_prohibited_token(raw_token)
        if category is not None:
            return category

    category = _detect_candidates(SPACED_TOKEN_PATTERN.findall(text))
    if category is not None:
        return category

    return _detect_candidates(SYMBOL_OBFUSCATION_PATTERN.findall(text))


def contains_prohibited_language(text: str) -> bool:
    return detect_prohibited_language(text) is not None


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

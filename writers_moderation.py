"""Scoped moderation for the writers and readers forum chat."""

from __future__ import annotations

import json
import logging
import os
import re
import time
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from aiogram.filters import BaseFilter
from aiogram.types import Chat, ChatMemberUpdated, LinkPreviewOptions, Message

LOGGER = logging.getLogger(__name__)
WRITERS_CHAT_USERNAME = os.getenv("WRITERS_CHAT_USERNAME", "chat_ikf").lstrip("@").lower()
WRITERS_RULES_URL = os.getenv(
    "WRITERS_RULES_URL",
    "https://t.me/" + "chat_IKF/168194/168197",
)
WARNING_COOLDOWN_SECONDS = max(10, int(os.getenv("WRITERS_WARNING_COOLDOWN_SECONDS", "60")))
RULES_LINK_PREVIEW_OPTIONS = LinkPreviewOptions(is_disabled=True)
# Explicit owner-approved exemptions. Only profanity deletion is skipped in
# these three Writers forum topics; all other chats/topics stay moderated.
WRITERS_PROFANITY_EXEMPT_TOPIC_IDS = frozenset({14637, 42817, 292358})
LEXICON_PATH = Path(__file__).resolve().parent / "data" / "moderation_lexicon.json"
MessageDeletedHook = Callable[[int, int], Awaitable[object]]

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

# Asterisks conceal letters rather than simply separating existing letters:
# 'бл*ть' must be compared against 'блять', not collapsed to 'блть'.
# Restrict candidates to a single word and demand visible characters on both
# sides, avoiding ordinary Markdown, lists, footnotes and arithmetic.
MASKED_STAR_CHARS = "*＊✱∗﹡"
MASKED_WORD_PATTERN = re.compile(
    rf"(?<![{TOKEN_CHAR_CLASS}])"
    rf"[{TOKEN_CHAR_CLASS}]+(?:[{re.escape(MASKED_STAR_CHARS)}]+[{TOKEN_CHAR_CLASS}]+)+"
    rf"(?![{TOKEN_CHAR_CLASS}])"
)
MASKED_STAR_RUN_PATTERN = re.compile(rf"[{re.escape(MASKED_STAR_CHARS)}]+")
# Explicit words, not generic stems. A broad '* matches anything' rule would
# delete innocent partially redacted names and fictional dialogue.
MASKED_OBSCENE_WORDS = (
    "блять", "блядь", "блядство", "блядский", "ебать", "ебал",
    "ебаный", "ебанутый", "ебнутый", "ебучий", "заебал",
    "заебало", "заебись", "наебал", "выебал", "пиздец",
    "пизда", "пиздеж", "пиздеть", "пиздатый", "пиздюк",
    "хуй", "хуя", "хуярить", "хуйня", "хуево", "нихуя",
    "нихуево", "нахуй", "похуй", "охуеть", "охуенно",
    "ахуеть", "сука", "сучка", "мудак", "мудила",
    "долбоеб", "уебок", "шлюха", "говно", "гавно",
    "дерьмо", "пидор", "пидорас", "пидарас",
    "гандон", "дебил", "сволочь", "мразь",
)
MASKED_LATIN_WORDS = (
    "blyat", "blyad", "ebat", "zaebal", "pizda",
    "pizdec", "pizdets", "huy", "huinya", "nahuy",
    "nihuya", "ohuet", "suka", "mudak", "dolboeb", "pidor",
    "pidoras", "govno", "gavno", "fuck", "fucking",
    "shit", "bitch", "cunt", "asshole",
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



def _masked_word_skeleton(value: str, *, latin: bool) -> str:
    normalizer = _normalize_latin_token if latin else _normalize_mixed_token
    return "*".join(
        normalizer(fragment) for fragment in MASKED_STAR_RUN_PATTERN.split(value)
    )


@lru_cache(maxsize=512)
def _masked_skeleton_regex(skeleton: str, *, latin: bool) -> re.Pattern[str]:
    # Each mask run stands for 1–5 missing letters; treat any count of
    # adjacent '*' as one placeholder, matching common Telegram censorship.
    alphabet = "[a-z0-9]" if latin else "[а-яa-z0-9]"
    return re.compile(re.escape(skeleton).replace(r"\*", f"{alphabet}{{1,5}}"))


def _detect_masked_profanity(text: str) -> str | None:
    for found in MASKED_WORD_PATTERN.finditer(text):
        token = found.group()
        # Bound CPU work and reject weak/ambiguous two-letter masks. The two
        # familiar unambiguous silhouettes х*й and п***ц are intentional.
        if len(token) > 48 or len(MASKED_STAR_RUN_PATTERN.findall(token)) > 4:
            continue
        pieces = MASKED_STAR_RUN_PATTERN.split(token)
        visible = sum(len(piece) for piece in pieces)
        mixed = _masked_word_skeleton(token, latin=False)
        if visible < 3:
            first, last = mixed.split("*", 1)[0], mixed.rsplit("*", 1)[-1]
            stars = sum(char in MASKED_STAR_CHARS for char in token)
            if not (
                (first == "х" and last == "й")
                or (first == "п" and last == "ц" and stars >= 2)
            ):
                continue
        for latin, dictionary in (
            (False, MASKED_OBSCENE_WORDS),
            (True, MASKED_LATIN_WORDS),
        ):
            skeleton = _masked_word_skeleton(token, latin=latin)
            # A Cyrillic fragment collapses to "" under the Latin-only
            # normalizer. Without this check, e.g. "с*ема" becomes "*"
            # and falsely matches any short English obscenity.
            if any(not piece for piece in skeleton.split("*")):
                continue
            pattern = _masked_skeleton_regex(skeleton, latin=latin)
            normalizer = _normalize_latin_token if latin else _normalize_mixed_token
            if any(pattern.fullmatch(normalizer(word)) for word in dictionary):
                return "obscene_masked"
    return None


def detect_prohibited_language(text: str) -> str | None:
    for raw_token in WORD_TOKEN_PATTERN.findall(text):
        category = _detect_prohibited_token(raw_token)
        if category is not None:
            return category

    category = _detect_masked_profanity(text)
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


class ProhibitedLanguageFilter(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        if getattr(message, "message_thread_id", None) in WRITERS_PROFANITY_EXEMPT_TOPIC_IDS:
            return False
        content = message.text or message.caption or ""
        user = getattr(message, "from_user", None)
        sender_chat = getattr(message, "sender_chat", None)
        # Telegram represents anonymous group administrators as a bot-like
        # sender. They are still subject to the chat's language rules.
        human_or_anonymous = (
            user is not None and (
                not bool(getattr(user, "is_bot", False))
                or sender_chat is not None
            )
        )
        return bool(
            human_or_anonymous
            and content
            and contains_prohibited_language(content)
        )


def register_writers_chat_handlers(
    module: Any,
    *,
    on_message_deleted: MessageDeletedHook | None = None,
) -> WritersChatScope:
    """Register only writers-chat moderation; Zero Trust owns all captcha flow."""
    scope = WritersChatScope()
    chat_filter = WritersChatFilter(scope, module.ALLOWED_CHATS)
    last_warning_at: dict[tuple[int, int], float] = {}

    @module.dp.message(chat_filter, ProhibitedLanguageFilter())
    async def remove_prohibited_language(message: Message) -> None:
        try:
            await message.delete()
        except Exception as exc:
            LOGGER.warning(
                "WRITERS_PROFANITY_DELETE_FAILED chat_id=%s topic_id=%s message_id=%s reason=%s",
                getattr(message.chat, "id", None),
                getattr(message, "message_thread_id", None),
                getattr(message, "message_id", None),
                type(exc).__name__,
            )
            return

        LOGGER.info(
            "WRITERS_PROFANITY_DELETED chat_id=%s topic_id=%s message_id=%s",
            getattr(message.chat, "id", None),
            getattr(message, "message_thread_id", None),
            getattr(message, "message_id", None),
        )

        if on_message_deleted is not None:
            try:
                await on_message_deleted(int(message.chat.id), int(message.message_id))
            except Exception as exc:
                LOGGER.warning(
                    "Could not purge deleted prohibited message from Entertainment memory: %s",
                    exc,
                )

        key = (message.chat.id, message.from_user.id)
        now = time.monotonic()
        previous_warning_at = last_warning_at.get(key)
        if (
            previous_warning_at is not None
            and now - previous_warning_at < WARNING_COOLDOWN_SECONDS
        ):
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

    module.dp.message.handlers.insert(0, module.dp.message.handlers.pop())
    return scope


def promote_writers_moderation_handler(dispatcher: Any) -> None:
    """Enforce moderation priority after all modules register their handlers.

    aiogram stops at the first matching handler. A newly promoted catch-all
    must never hide the Writers profanity deletion handler.
    """
    handlers = getattr(getattr(dispatcher, "message", None), "handlers", None)
    if not isinstance(handlers, list):
        raise RuntimeError("Writers moderation dispatcher is not initialized")
    for position, record in enumerate(handlers):
        if getattr(getattr(record, "callback", None), "__name__", "") == "remove_prohibited_language":
            if position:
                handlers.insert(0, handlers.pop(position))
            LOGGER.info(
                "WRITERS_MODERATION_PRIORITY_READY first=remove_prohibited_language handlers=%s",
                len(handlers),
            )
            return
    raise RuntimeError("Writers profanity moderation handler was not registered")


async def report_writers_delete_permission(bot: Any, chat_id: int | None) -> None:
    """Best-effort startup check; failures never disable moderation."""
    if chat_id is None:
        LOGGER.warning("WRITERS_MODERATION_SCOPE_MISSING writers_chat_id=unresolved")
        return
    try:
        member = await bot.get_chat_member(chat_id=int(chat_id), user_id=int(bot.id))
    except Exception as exc:
        LOGGER.warning(
            "WRITERS_MODERATION_PERMISSION_CHECK_FAILED chat_id=%s reason=%s",
            chat_id,
            type(exc).__name__,
        )
        return
    raw_status = getattr(member, "status", "")
    status = str(getattr(raw_status, "value", raw_status)).lower()
    can_delete = status in {"creator", "owner"} or (
        status == "administrator" and bool(getattr(member, "can_delete_messages", False))
    )
    if can_delete:
        LOGGER.info("WRITERS_MODERATION_DELETE_PERMISSION_READY chat_id=%s", chat_id)
    else:
        LOGGER.error(
            "WRITERS_MODERATION_DELETE_PERMISSION_MISSING chat_id=%s bot_status=%s "
            "grant bot administrator role with Delete messages permission",
            chat_id,
            status,
        )

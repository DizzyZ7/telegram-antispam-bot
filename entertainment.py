"""Scoped entertainment layer with per-chat learning and lightweight generation.

The module is deliberately disabled unless a chat id is present in
ENTERTAINMENT_CHAT_IDS. Learned data never crosses chat boundaries.

V1 intentionally has no external AI dependency:
- remembers eligible text messages per enabled chat;
- generates new phrases with a small Markov-style model;
- can reply spontaneously with configurable "laziness";
- exposes admin controls for enable/disable, laziness, cooldown and memory reset.

Future meme/image/voice/comic providers can plug into the same scoped service.
"""

from __future__ import annotations

import html
import logging
import os
import random
import re
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiosqlite
from aiogram import BaseMiddleware, F
from aiogram.filters import BaseFilter, Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

LOGGER = logging.getLogger(__name__)

DEFAULT_LAZINESS = 92
DEFAULT_COOLDOWN_SECONDS = 45
MEMORY_LIMIT = 5_000
GENERATION_SAMPLE_LIMIT = 900
MIN_MESSAGES_TO_GENERATE = 25
MIN_MESSAGE_LENGTH = 3
MAX_MESSAGE_LENGTH = 600
MAX_GENERATED_TOKENS = 30

TOKEN_RE = re.compile(
    r"[A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9_'’-]*|[.,!?…:;]",
    re.UNICODE,
)
URL_RE = re.compile(r"(?:https?://|www\.|t\.me/)", re.IGNORECASE)
CHAT_ID_SPLIT_RE = re.compile(r"[\s,;]+")
START_TOKEN = "<START>"
END_TOKEN = "<END>"


def parse_chat_ids(raw_value: str | None) -> frozenset[int]:
    """Parse comma/space/semicolon separated Telegram chat ids."""
    if not raw_value:
        return frozenset()

    result: set[int] = set()
    for item in CHAT_ID_SPLIT_RE.split(raw_value.strip()):
        if not item:
            continue
        try:
            result.add(int(item))
        except ValueError:
            LOGGER.warning("Ignoring invalid ENTERTAINMENT_CHAT_IDS item: %r", item)
    return frozenset(result)


ENTERTAINMENT_CHAT_IDS = parse_chat_ids(os.getenv("ENTERTAINMENT_CHAT_IDS"))


@dataclass(frozen=True, slots=True)
class EntertainmentSettings:
    enabled: bool = True
    laziness: int = DEFAULT_LAZINESS
    cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS

    @property
    def spontaneous_chance_percent(self) -> int:
        return max(0, min(100, 100 - self.laziness))


class EntertainmentStorage:
    """SQLite-backed chat settings and isolated learning memory."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.connection: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = await aiosqlite.connect(self.database_path)
        await self.connection.execute("PRAGMA journal_mode=WAL;")
        await self.connection.execute("PRAGMA synchronous=NORMAL;")
        await self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS entertainment_chat_settings (
                chat_id INTEGER PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 1,
                laziness INTEGER NOT NULL DEFAULT 92,
                cooldown_seconds INTEGER NOT NULL DEFAULT 45,
                updated_at INTEGER NOT NULL
            )
            """
        )
        await self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS entertainment_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                text TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )
        await self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_entertainment_messages_chat_id "
            "ON entertainment_messages(chat_id, id DESC)"
        )
        await self.connection.commit()

    async def close(self) -> None:
        if self.connection is None:
            return
        await self.connection.close()
        self.connection = None

    def _require_connection(self) -> aiosqlite.Connection:
        if self.connection is None:
            raise RuntimeError("Entertainment storage is not initialized")
        return self.connection

    async def get_settings(self, chat_id: int) -> EntertainmentSettings:
        connection = self._require_connection()
        async with connection.execute(
            """
            SELECT enabled, laziness, cooldown_seconds
            FROM entertainment_chat_settings
            WHERE chat_id = ?
            """,
            (int(chat_id),),
        ) as cursor:
            row = await cursor.fetchone()

        if row is None:
            return EntertainmentSettings()

        return EntertainmentSettings(
            enabled=bool(row[0]),
            laziness=max(0, min(100, int(row[1]))),
            cooldown_seconds=max(5, int(row[2])),
        )

    async def save_settings(self, chat_id: int, settings: EntertainmentSettings) -> None:
        connection = self._require_connection()
        await connection.execute(
            """
            INSERT INTO entertainment_chat_settings(
                chat_id, enabled, laziness, cooldown_seconds, updated_at
            )
            VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                enabled = excluded.enabled,
                laziness = excluded.laziness,
                cooldown_seconds = excluded.cooldown_seconds,
                updated_at = excluded.updated_at
            """,
            (
                int(chat_id),
                1 if settings.enabled else 0,
                max(0, min(100, int(settings.laziness))),
                max(5, int(settings.cooldown_seconds)),
                int(time.time()),
            ),
        )
        await connection.commit()

    async def add_message(self, chat_id: int, user_id: int, text: str) -> None:
        connection = self._require_connection()
        await connection.execute(
            """
            INSERT INTO entertainment_messages(chat_id, user_id, text, created_at)
            VALUES(?, ?, ?, ?)
            """,
            (int(chat_id), int(user_id), text, int(time.time())),
        )

        # Keep each chat isolated and bounded. This avoids a global unbounded
        # transcript while retaining enough material for a recognizable style.
        await connection.execute(
            """
            DELETE FROM entertainment_messages
            WHERE chat_id = ?
              AND id NOT IN (
                  SELECT id
                  FROM entertainment_messages
                  WHERE chat_id = ?
                  ORDER BY id DESC
                  LIMIT ?
              )
            """,
            (int(chat_id), int(chat_id), MEMORY_LIMIT),
        )
        await connection.commit()

    async def recent_messages(self, chat_id: int, limit: int = GENERATION_SAMPLE_LIMIT) -> list[str]:
        connection = self._require_connection()
        async with connection.execute(
            """
            SELECT text
            FROM entertainment_messages
            WHERE chat_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (int(chat_id), max(1, int(limit))),
        ) as cursor:
            rows = await cursor.fetchall()
        return [str(row[0]) for row in reversed(rows)]

    async def message_count(self, chat_id: int) -> int:
        connection = self._require_connection()
        async with connection.execute(
            "SELECT COUNT(*) FROM entertainment_messages WHERE chat_id = ?",
            (int(chat_id),),
        ) as cursor:
            row = await cursor.fetchone()
        return int(row[0]) if row else 0

    async def clear_chat(self, chat_id: int) -> int:
        connection = self._require_connection()
        async with connection.execute(
            "SELECT COUNT(*) FROM entertainment_messages WHERE chat_id = ?",
            (int(chat_id),),
        ) as cursor:
            row = await cursor.fetchone()
        count = int(row[0]) if row else 0
        await connection.execute(
            "DELETE FROM entertainment_messages WHERE chat_id = ?",
            (int(chat_id),),
        )
        await connection.commit()
        return count


class EntertainmentChatFilter(BaseFilter):
    """Match only explicitly allowlisted entertainment chats."""

    __slots__ = ("chat_ids",)

    def __init__(self, chat_ids: Iterable[int]) -> None:
        self.chat_ids = frozenset(int(chat_id) for chat_id in chat_ids)

    async def __call__(self, message: Message) -> bool:
        return int(message.chat.id) in self.chat_ids


def _tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall(text)


def _detokenize(tokens: list[str]) -> str:
    if not tokens:
        return ""

    no_space_before = {".", ",", "!", "?", "…", ":", ";"}
    result = ""
    for token in tokens:
        if not result:
            result = token
        elif token in no_space_before:
            result += token
        else:
            result += " " + token

    if result and result[-1] not in ".!?…":
        result += "."
    return result


def _normalized_for_comparison(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().casefold())


def generate_chat_text(
    messages: list[str],
    *,
    rng: random.Random | None = None,
    max_tokens: int = MAX_GENERATED_TOKENS,
) -> str | None:
    """Generate a new phrase from one chat without mixing data across chats."""
    rng = rng or random.Random()

    tokenized: list[list[str]] = []
    originals: set[str] = set()
    for message in messages:
        tokens = _tokenize(message)
        if len(tokens) < 2:
            continue
        tokenized.append(tokens)
        originals.add(_normalized_for_comparison(_detokenize(tokens)))

    if len(tokenized) < MIN_MESSAGES_TO_GENERATE:
        return None

    transitions: dict[str, list[str]] = defaultdict(list)
    for tokens in tokenized:
        previous = START_TOKEN
        for token in tokens:
            transitions[previous].append(token)
            previous = token.casefold()
        transitions[previous].append(END_TOKEN)

    if not transitions.get(START_TOKEN):
        return None

    # Try several times to avoid simply reproducing an existing message.
    for _ in range(8):
        output: list[str] = []
        current = START_TOKEN

        for _step in range(max(4, int(max_tokens))):
            options = transitions.get(current)
            if not options:
                break
            token = rng.choice(options)
            if token == END_TOKEN:
                if len(output) >= 4:
                    break
                current = START_TOKEN
                continue
            output.append(token)
            current = token.casefold()

        generated = _detokenize(output).strip()
        normalized = _normalized_for_comparison(generated)
        if len(output) >= 4 and generated and normalized not in originals:
            return generated

    return None


class EntertainmentService:
    """Per-chat entertainment behavior with strict allowlist isolation."""

    def __init__(
        self,
        app: Any,
        storage: EntertainmentStorage,
        chat_ids: Iterable[int],
        *,
        rng: random.Random | None = None,
    ) -> None:
        self.app = app
        self.storage = storage
        self.chat_ids = frozenset(int(chat_id) for chat_id in chat_ids)
        self.rng = rng or random.Random()
        self._last_spontaneous_reply_at: dict[int, float] = {}

    def is_allowed_chat(self, chat_id: int) -> bool:
        return int(chat_id) in self.chat_ids

    @staticmethod
    def _chat_type_value(message: Message) -> str:
        chat_type = getattr(message.chat, "type", "")
        return str(getattr(chat_type, "value", chat_type))

    def is_eligible_learning_message(self, message: Message) -> bool:
        if not self.is_allowed_chat(message.chat.id):
            return False
        if self._chat_type_value(message) not in {"group", "supergroup"}:
            return False
        if message.from_user is None or message.from_user.is_bot:
            return False
        if not message.text:
            return False

        text = message.text.strip()
        if text.startswith("/"):
            return False
        if len(text) < MIN_MESSAGE_LENGTH or len(text) > MAX_MESSAGE_LENGTH:
            return False
        if URL_RE.search(text):
            return False
        if len(_tokenize(text)) < 2:
            return False
        return True

    async def _is_admin(self, message: Message) -> bool:
        if message.from_user is None:
            return False
        try:
            member = await self.app.bot.get_chat_member(
                chat_id=message.chat.id,
                user_id=message.from_user.id,
            )
        except Exception:
            LOGGER.info(
                "Could not check entertainment admin status chat_id=%s user_id=%s",
                message.chat.id,
                message.from_user.id,
                exc_info=True,
            )
            return False
        return getattr(member, "status", None) in {"creator", "administrator"}

    async def observe_message(self, message: Message) -> None:
        """Learn a message and optionally emit a spontaneous generated reply."""
        if not self.is_eligible_learning_message(message):
            return

        settings = await self.storage.get_settings(message.chat.id)
        if not settings.enabled:
            return

        text = message.text.strip()
        await self.storage.add_message(
            chat_id=message.chat.id,
            user_id=message.from_user.id,
            text=text,
        )

        now = time.monotonic()
        previous_reply = self._last_spontaneous_reply_at.get(message.chat.id, 0.0)
        if now - previous_reply < settings.cooldown_seconds:
            return

        if self.rng.randrange(100) < settings.laziness:
            return

        messages = await self.storage.recent_messages(message.chat.id)
        generated = generate_chat_text(messages, rng=self.rng)
        if not generated:
            return

        self._last_spontaneous_reply_at[message.chat.id] = now
        await message.reply(html.escape(generated))
        LOGGER.info(
            "ENTERTAINMENT_SPONTANEOUS_REPLY chat_id=%s memory=%s laziness=%s",
            message.chat.id,
            len(messages),
            settings.laziness,
        )

    @staticmethod
    def panel_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="🎲 Сгенерировать реплику", callback_data="fun:generate"),
                ],
                [
                    InlineKeyboardButton(text="🧠 Память и статус", callback_data="fun:status"),
                ],
            ]
        )

    async def show_panel(self, message: Message) -> None:
        settings = await self.storage.get_settings(message.chat.id)
        count = await self.storage.message_count(message.chat.id)
        state = "включен" if settings.enabled else "выключен"
        await message.reply(
            "🎭 <b>Развлекательный режим</b>\n\n"
            f"Состояние: <b>{state}</b>\n"
            f"Память этого чата: <b>{count}</b>/{MEMORY_LIMIT}\n"
            f"Лень: <b>{settings.laziness}%</b> "
            f"(сам ответит примерно в {settings.spontaneous_chance_percent}% подходящих случаев)\n"
            f"Кулдаун случайных ответов: <b>{settings.cooldown_seconds} сек.</b>\n\n"
            "Каждый чат обучается отдельно. Данные между чатами не смешиваются.\n\n"
            "Админам: /fun_on · /fun_off · /fun_laziness 0-100 · "
            "/fun_cooldown 5-3600 · /fun_forget",
            reply_markup=self.panel_keyboard(),
        )

    async def show_status(self, message: Message) -> None:
        await self.show_panel(message)

    async def generate_now(self, message: Message) -> None:
        settings = await self.storage.get_settings(message.chat.id)
        if not settings.enabled:
            await message.reply("🎭 Развлекательный режим сейчас выключен администратором.")
            return

        count = await self.storage.message_count(message.chat.id)
        if count < MIN_MESSAGES_TO_GENERATE:
            missing = MIN_MESSAGES_TO_GENERATE - count
            await message.reply(
                "🧠 Мне пока мало материала именно из этого чата.\n"
                f"Нужно еще примерно <b>{missing}</b> подходящих сообщений."
            )
            return

        messages = await self.storage.recent_messages(message.chat.id)
        generated = generate_chat_text(messages, rng=self.rng)
        if not generated:
            await message.reply(
                "🧠 Материал уже есть, но сейчас не получилось собрать нормальную новую фразу. "
                "Попробуй еще раз после нескольких сообщений."
            )
            return
        await message.reply(html.escape(generated))

    async def set_enabled(self, message: Message, enabled: bool) -> None:
        if not await self._is_admin(message):
            await message.reply("⚙️ Эту настройку может менять только администрация чата.")
            return
        current = await self.storage.get_settings(message.chat.id)
        updated = EntertainmentSettings(
            enabled=enabled,
            laziness=current.laziness,
            cooldown_seconds=current.cooldown_seconds,
        )
        await self.storage.save_settings(message.chat.id, updated)
        await message.reply(
            "🎭 Развлекательный режим <b>включен</b>."
            if enabled
            else "🎭 Развлекательный режим <b>выключен</b>."
        )

    async def set_laziness(self, message: Message) -> None:
        if not await self._is_admin(message):
            await message.reply("⚙️ Лень может менять только администрация чата.")
            return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2:
            await message.reply("Использование: <code>/fun_laziness 0-100</code>")
            return
        try:
            value = int(parts[1].strip())
        except ValueError:
            await message.reply("Лень должна быть целым числом от 0 до 100.")
            return
        if not 0 <= value <= 100:
            await message.reply("Лень должна быть от 0 до 100.")
            return

        current = await self.storage.get_settings(message.chat.id)
        updated = EntertainmentSettings(
            enabled=current.enabled,
            laziness=value,
            cooldown_seconds=current.cooldown_seconds,
        )
        await self.storage.save_settings(message.chat.id, updated)
        await message.reply(
            f"😴 Лень теперь <b>{value}%</b>. "
            f"Шанс самопроизвольной реплики — примерно <b>{100 - value}%</b>."
        )

    async def set_cooldown(self, message: Message) -> None:
        if not await self._is_admin(message):
            await message.reply("⚙️ Кулдаун может менять только администрация чата.")
            return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2:
            await message.reply("Использование: <code>/fun_cooldown 5-3600</code>")
            return
        try:
            value = int(parts[1].strip())
        except ValueError:
            await message.reply("Кулдаун должен быть целым числом секунд.")
            return
        if not 5 <= value <= 3600:
            await message.reply("Кулдаун должен быть от 5 до 3600 секунд.")
            return

        current = await self.storage.get_settings(message.chat.id)
        updated = EntertainmentSettings(
            enabled=current.enabled,
            laziness=current.laziness,
            cooldown_seconds=value,
        )
        await self.storage.save_settings(message.chat.id, updated)
        await message.reply(f"⏱ Кулдаун случайных реплик теперь <b>{value} сек.</b>")

    async def forget_chat(self, message: Message) -> None:
        if not await self._is_admin(message):
            await message.reply("🧠 Стирать память чата может только администрация.")
            return
        removed = await self.storage.clear_chat(message.chat.id)
        self._last_spontaneous_reply_at.pop(message.chat.id, None)
        await message.reply(
            f"🧠 Память этого чата очищена. Удалено сообщений: <b>{removed}</b>. "
            "Другие чаты не затронуты."
        )

    async def handle_callback(self, callback: CallbackQuery) -> None:
        if callback.message is None:
            await callback.answer()
            return

        chat = getattr(callback.message, "chat", None)
        if chat is None or not self.is_allowed_chat(chat.id):
            await callback.answer("Здесь развлекательный режим недоступен.", show_alert=True)
            return

        data = callback.data or ""
        if data == "fun:generate":
            await callback.answer("Собираю фразу…")
            # aiogram's Message-like callback message is compatible with the methods used.
            await self.generate_now(callback.message)  # type: ignore[arg-type]
            return
        if data == "fun:status":
            await callback.answer()
            await self.show_status(callback.message)  # type: ignore[arg-type]
            return
        await callback.answer()


class EntertainmentLearningMiddleware(BaseMiddleware):
    """Observe allowlisted chat messages without consuming other bot handlers."""

    def __init__(self, service: EntertainmentService) -> None:
        self.service = service

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        try:
            await self.service.observe_message(event)
        except Exception:
            LOGGER.exception(
                "Could not process entertainment learning chat_id=%s",
                getattr(getattr(event, "chat", None), "id", None),
            )
        return await handler(event, data)


def _promote_last_message_handler(app: Any) -> None:
    handler = app.dp.message.handlers.pop()
    app.dp.message.handlers.insert(0, handler)


def register_entertainment_handlers(app: Any, service: EntertainmentService) -> None:
    """Register scoped entertainment handlers; no allowlisted ids means no effect."""
    dispatcher = app.dp
    dispatcher.message.outer_middleware(EntertainmentLearningMiddleware(service))
    chat_filter = EntertainmentChatFilter(service.chat_ids)

    @dispatcher.message(chat_filter, Command(commands=["fun", "entertainment"]))
    async def entertainment_panel(message: Message) -> None:
        await service.show_panel(message)

    _promote_last_message_handler(app)

    @dispatcher.message(chat_filter, Command(commands=["fun_generate", "fun_gen"]))
    async def entertainment_generate(message: Message) -> None:
        await service.generate_now(message)

    _promote_last_message_handler(app)

    @dispatcher.message(chat_filter, Command(commands=["fun_on"]))
    async def entertainment_on(message: Message) -> None:
        await service.set_enabled(message, True)

    _promote_last_message_handler(app)

    @dispatcher.message(chat_filter, Command(commands=["fun_off"]))
    async def entertainment_off(message: Message) -> None:
        await service.set_enabled(message, False)

    _promote_last_message_handler(app)

    @dispatcher.message(chat_filter, Command(commands=["fun_laziness"]))
    async def entertainment_laziness(message: Message) -> None:
        await service.set_laziness(message)

    _promote_last_message_handler(app)

    @dispatcher.message(chat_filter, Command(commands=["fun_cooldown"]))
    async def entertainment_cooldown(message: Message) -> None:
        await service.set_cooldown(message)

    _promote_last_message_handler(app)

    @dispatcher.message(chat_filter, Command(commands=["fun_forget"]))
    async def entertainment_forget(message: Message) -> None:
        await service.forget_chat(message)

    _promote_last_message_handler(app)

    @dispatcher.callback_query(F.data.startswith("fun:"))
    async def entertainment_callback(callback: CallbackQuery) -> None:
        await service.handle_callback(callback)

    ids_label = ",".join(str(chat_id) for chat_id in sorted(service.chat_ids)) or "none"
    print(
        "ENTERTAINMENT_SCOPE_READY "
        f"chat_ids={ids_label} learning=per_chat spontaneous=on external_ai=off",
        flush=True,
    )


__all__ = [
    "DEFAULT_COOLDOWN_SECONDS",
    "DEFAULT_LAZINESS",
    "ENTERTAINMENT_CHAT_IDS",
    "EntertainmentChatFilter",
    "EntertainmentService",
    "EntertainmentSettings",
    "EntertainmentStorage",
    "MEMORY_LIMIT",
    "generate_chat_text",
    "parse_chat_ids",
    "register_entertainment_handlers",
]

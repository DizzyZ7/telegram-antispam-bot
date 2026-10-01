"""Entertainment service and the v1-compatible SQLite storage."""

from __future__ import annotations

import html
import logging
import random
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import aiosqlite
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .config import (
    GENERATION_SAMPLE_LIMIT,
    MAX_MESSAGE_LENGTH,
    MEMORY_LIMIT,
    MIN_MESSAGE_LENGTH,
    MIN_MESSAGES_TO_GENERATE,
    URL_RE,
)
from .generation import generate_chat_text, tokenize
from .models import EntertainmentSettings

LOGGER = logging.getLogger(__name__)


class EntertainmentStorage:
    """SQLite-backed chat settings and isolated learning memory.

    This concrete v1-compatible class intentionally remains unchanged during
    the package split. Task 2 replaces it with a storage protocol plus an
    explicit SQLite backend.
    """

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
        if len(tokenize(text)) < 2:
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
                [InlineKeyboardButton(text="🎲 Сгенерировать реплику", callback_data="fun:generate")],
                [InlineKeyboardButton(text="🧠 Память и статус", callback_data="fun:status")],
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
            await self.generate_now(callback.message)  # type: ignore[arg-type]
            return
        if data == "fun:status":
            await callback.answer()
            await self.show_status(callback.message)  # type: ignore[arg-type]
            return
        await callback.answer()

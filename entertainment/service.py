"""Topic-aware entertainment service."""

from __future__ import annotations

import html
import logging
import random
import time
from collections.abc import Iterable
from typing import Any

from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .config import MAX_MESSAGE_LENGTH, MEMORY_LIMIT, MIN_MESSAGE_LENGTH, MIN_MESSAGES_TO_GENERATE, URL_RE
from .generation import generate_chat_text, tokenize
from .models import EntertainmentSettings, normalize_topic_id
from .storage.base import EntertainmentStorage

LOGGER = logging.getLogger(__name__)


class EntertainmentService:
    """Per-chat entertainment behavior with strict allowlist and topic isolation."""

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
        self._last_spontaneous_reply_at: dict[tuple[int, int], float] = {}

    def is_allowed_chat(self, chat_id: int) -> bool:
        return int(chat_id) in self.chat_ids

    @staticmethod
    def _chat_type_value(message: Message) -> str:
        chat_type = getattr(message.chat, "type", "")
        return str(getattr(chat_type, "value", chat_type))

    @staticmethod
    def _topic_id(message: Message) -> int:
        return normalize_topic_id(getattr(message, "message_thread_id", None))

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

        topic_id = self._topic_id(message)
        text = message.text.strip()
        await self.storage.add_message(
            chat_id=message.chat.id,
            topic_id=topic_id,
            user_id=message.from_user.id,
            text=text,
            message_id=getattr(message, "message_id", None),
        )

        key = (int(message.chat.id), topic_id)
        now = time.monotonic()
        previous_reply = self._last_spontaneous_reply_at.get(key, 0.0)
        if now - previous_reply < settings.cooldown_seconds:
            return
        if self.rng.randrange(100) < settings.laziness:
            return

        messages = await self.storage.recent_messages(message.chat.id, topic_id)
        generated = generate_chat_text(messages, rng=self.rng)
        if not generated:
            return

        self._last_spontaneous_reply_at[key] = now
        await message.reply(html.escape(generated))
        LOGGER.info(
            "ENTERTAINMENT_SPONTANEOUS_REPLY chat_id=%s topic_id=%s memory=%s",
            message.chat.id,
            topic_id,
            len(messages),
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
        topic_id = self._topic_id(message)
        count = await self.storage.message_count(message.chat.id, topic_id)
        state = "включен" if settings.enabled else "выключен"
        await message.reply(
            "🎭 <b>Развлекательный режим</b>\n\n"
            f"Состояние: <b>{state}</b>\n"
            f"Память этой темы: <b>{count}</b>/{MEMORY_LIMIT}\n"
            f"Лень: <b>{settings.laziness}%</b> "
            f"(сам ответит примерно в {settings.spontaneous_chance_percent}% подходящих случаев)\n"
            f"Кулдаун случайных ответов: <b>{settings.cooldown_seconds} сек.</b>\n\n"
            "Темы форума обучаются отдельно. Данные между чатами не смешиваются.\n\n"
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
        topic_id = self._topic_id(message)
        count = await self.storage.message_count(message.chat.id, topic_id)
        if count < MIN_MESSAGES_TO_GENERATE:
            missing = MIN_MESSAGES_TO_GENERATE - count
            await message.reply(
                "🧠 Мне пока мало материала именно из этой темы.\n"
                f"Нужно еще примерно <b>{missing}</b> подходящих сообщений."
            )
            return
        messages = await self.storage.recent_messages(message.chat.id, topic_id)
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
        await self.storage.save_settings(
            message.chat.id,
            EntertainmentSettings(
                enabled=enabled,
                laziness=current.laziness,
                cooldown_seconds=current.cooldown_seconds,
            ),
        )
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
        await self.storage.save_settings(
            message.chat.id,
            EntertainmentSettings(
                enabled=current.enabled,
                laziness=value,
                cooldown_seconds=current.cooldown_seconds,
            ),
        )
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
        await self.storage.save_settings(
            message.chat.id,
            EntertainmentSettings(
                enabled=current.enabled,
                laziness=current.laziness,
                cooldown_seconds=value,
            ),
        )
        await message.reply(f"⏱ Кулдаун случайных реплик теперь <b>{value} сек.</b>")

    async def forget_chat(self, message: Message) -> None:
        if not await self._is_admin(message):
            await message.reply("🧠 Стирать память темы может только администрация.")
            return
        topic_id = self._topic_id(message)
        removed = await self.storage.clear_scope(message.chat.id, topic_id)
        self._last_spontaneous_reply_at.pop((int(message.chat.id), topic_id), None)
        await message.reply(
            f"🧠 Память этой темы очищена. Удалено сообщений: <b>{removed}</b>. "
            "Другие темы и чаты не затронуты."
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

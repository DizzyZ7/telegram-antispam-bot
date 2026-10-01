"""Topic-aware entertainment service."""

from __future__ import annotations

import html
import logging
import random
import time
from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .autonomy import ActionCandidate, ConversationPhase, DecisionContext, derive_phase, select_action
from .config import MAX_MESSAGE_LENGTH, MEMORY_LIMIT, MIN_MESSAGE_LENGTH, MIN_MESSAGES_TO_GENERATE, URL_RE
from .generation import generate_chat_text, tokenize
from .models import (
    EntertainmentActionRecord,
    EntertainmentActionType,
    EntertainmentSettings,
    normalize_topic_id,
)
from .novelty import is_novel_generated_text
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
        now_fn: Callable[[], float] | None = None,
    ) -> None:
        self.app = app
        self.storage = storage
        self.chat_ids = frozenset(int(chat_id) for chat_id in chat_ids)
        self.rng = rng or random.Random()
        self._now_fn = now_fn or time.time

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

    def _is_direct_trigger(self, message: Message) -> bool:
        replied = getattr(message, "reply_to_message", None)
        replied_user = getattr(replied, "from_user", None)
        if replied_user is None or not getattr(replied_user, "is_bot", False):
            return False
        bot_id = getattr(getattr(self.app, "bot", None), "id", None)
        return bot_id is not None and getattr(replied_user, "id", None) == bot_id

    @staticmethod
    def _quiet_hours_active(settings: EntertainmentSettings, now: int) -> bool:
        start = settings.quiet_hours_start
        end = settings.quiet_hours_end
        if start is None or end is None or start == end:
            return False
        try:
            timezone = ZoneInfo(settings.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            timezone = ZoneInfo("Europe/Moscow")
        hour = datetime.fromtimestamp(int(now), timezone).hour
        if start < end:
            return start <= hour < end
        return hour >= start or hour < end

    @staticmethod
    def _phase_candidate(phase: ConversationPhase) -> ActionCandidate:
        profiles = {
            ConversationPhase.QUIET: (0.58, 0.80, 0.18),
            ConversationPhase.WARMING_UP: (0.72, 0.82, 0.12),
            ConversationPhase.ACTIVE: (0.62, 0.80, 0.28),
            ConversationPhase.PEAK: (0.30, 0.75, 0.85),
            ConversationPhase.COOLDOWN: (0.88, 0.86, 0.08),
        }
        relevance, novelty, annoyance = profiles[phase]
        return ActionCandidate(
            action_type=EntertainmentActionType.REMIXED_PHRASE,
            relevance=relevance,
            novelty=novelty,
            annoyance_cost=annoyance,
        )

    def _build_candidates(self, phase: ConversationPhase, trigger_message: Message) -> list[ActionCandidate]:
        candidates = [self._phase_candidate(phase)]
        if self._is_direct_trigger(trigger_message):
            candidates.append(
                ActionCandidate(
                    action_type=EntertainmentActionType.CONTEXTUAL_REPLY,
                    relevance=0.97,
                    novelty=0.88,
                    annoyance_cost=0.03,
                    trigger_message_id=getattr(trigger_message, "message_id", None),
                )
            )
        return candidates

    def _generate_novel_text(
        self,
        messages: list[str],
        recent_outputs: list[str],
    ) -> str | None:
        for _ in range(5):
            generated = generate_chat_text(messages, rng=self.rng)
            if not generated:
                continue
            if is_novel_generated_text(generated, messages, recent_outputs):
                return generated
        return None

    async def evaluate_topic(self, message: Message) -> EntertainmentActionRecord | None:
        chat_id = int(message.chat.id)
        topic_id = self._topic_id(message)
        now = int(self._now_fn())
        settings = await self.storage.get_settings(chat_id)
        if not settings.enabled or not settings.autonomous_text_enabled:
            return None

        memory_count = await self.storage.message_count(chat_id, topic_id)
        if memory_count < MIN_MESSAGES_TO_GENERATE:
            return None

        activity = await self.storage.activity_snapshot(chat_id, topic_id, now=now)
        phase = derive_phase(activity)
        recent_actions = tuple(
            await self.storage.recent_actions(
                chat_id,
                topic_id,
                since=now - 30 * 60,
                limit=20,
            )
        )
        last_action = recent_actions[0] if recent_actions else None
        human_messages = (
            await self.storage.human_messages_since(
                chat_id,
                topic_id,
                since=int(last_action.created_at),
            )
            if last_action is not None
            else 0
        )
        context = DecisionContext(
            settings=settings,
            phase=phase,
            activity=activity,
            recent_actions=recent_actions,
            human_messages_since_last_action=human_messages,
            memory_count=memory_count,
            quiet_hours_active=self._quiet_hours_active(settings, now),
            now=now,
        )
        selected = select_action(
            context,
            self._build_candidates(phase, message),
            rng=self.rng,
        )
        if selected is None:
            return None

        messages = await self.storage.recent_messages(chat_id, topic_id)
        recent_outputs = [
            output
            for action in recent_actions
            if isinstance((output := action.metadata.get("output")), str) and output
        ]
        generated = self._generate_novel_text(messages, recent_outputs)
        if generated is None:
            return None

        await message.reply(html.escape(generated))
        record = EntertainmentActionRecord(
            id=None,
            chat_id=chat_id,
            topic_id=topic_id,
            action_type=selected.action_type,
            trigger_message_id=(
                selected.trigger_message_id
                if selected.trigger_message_id is not None
                else getattr(message, "message_id", None)
            ),
            created_at=now,
            metadata={
                "phase": phase.value,
                "mode": settings.behavior_mode.value,
                "output": generated,
                "memory_count": memory_count,
            },
        )
        action_id = await self.storage.record_action(record)
        stored_record = replace(record, id=action_id)
        LOGGER.info(
            "ENTERTAINMENT_AUTONOMOUS_ACTION chat_id=%s topic_id=%s action=%s phase=%s mode=%s memory=%s",
            chat_id,
            topic_id,
            selected.action_type.value,
            phase.value,
            settings.behavior_mode.value,
            memory_count,
        )
        return stored_record

    async def observe_message(self, message: Message) -> None:
        if not self.is_eligible_learning_message(message):
            return
        settings = await self.storage.get_settings(message.chat.id)
        if not settings.enabled:
            return

        topic_id = self._topic_id(message)
        text = message.text.strip()
        now = int(self._now_fn())
        await self.storage.add_message(
            chat_id=message.chat.id,
            topic_id=topic_id,
            user_id=message.from_user.id,
            text=text,
            message_id=getattr(message, "message_id", None),
            created_at=now,
        )
        await self.evaluate_topic(message)

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
            f"(legacy-настройка; Autonomy v2 ее не использует)\n"
            f"Режим поведения: <b>{settings.behavior_mode.display_name}</b>\n\n"
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
        recent_actions = await self.storage.recent_actions(
            message.chat.id,
            topic_id,
            since=int(self._now_fn()) - 30 * 60,
            limit=20,
        )
        recent_outputs = [
            output
            for action in recent_actions
            if isinstance((output := action.metadata.get("output")), str) and output
        ]
        generated = self._generate_novel_text(messages, recent_outputs)
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
        await self.storage.save_settings(message.chat.id, replace(current, enabled=enabled))
        await message.reply(
            "🎭 Развлекательный режим <b>включен</b>."
            if enabled
            else "🎭 Развлекательный режим <b>выключен</b>."
        )

    async def set_laziness(self, message: Message) -> None:
        if not await self._is_admin(message):
            await message.reply("⚙️ Legacy-настройку может менять только администрация чата.")
            return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2:
            await message.reply("Использование: <code>/fun_laziness 0-100</code>")
            return
        try:
            value = int(parts[1].strip())
        except ValueError:
            await message.reply("Значение должно быть целым числом от 0 до 100.")
            return
        if not 0 <= value <= 100:
            await message.reply("Значение должно быть от 0 до 100.")
            return
        current = await self.storage.get_settings(message.chat.id)
        await self.storage.save_settings(message.chat.id, replace(current, laziness=value))
        await message.reply(
            f"⚙️ Legacy-параметр сохранен: <b>{value}%</b>. "
            "Autonomy v2 не использует его для решений."
        )

    async def set_cooldown(self, message: Message) -> None:
        if not await self._is_admin(message):
            await message.reply("⚙️ Legacy-настройку может менять только администрация чата.")
            return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2:
            await message.reply("Использование: <code>/fun_cooldown 5-3600</code>")
            return
        try:
            value = int(parts[1].strip())
        except ValueError:
            await message.reply("Значение должно быть целым числом секунд.")
            return
        if not 5 <= value <= 3600:
            await message.reply("Значение должно быть от 5 до 3600 секунд.")
            return
        current = await self.storage.get_settings(message.chat.id)
        await self.storage.save_settings(message.chat.id, replace(current, cooldown_seconds=value))
        await message.reply(
            f"⚙️ Legacy-кулдаун сохранен: <b>{value} сек.</b> "
            "Autonomy v2 использует собственные бюджеты режима."
        )

    async def forget_chat(self, message: Message) -> None:
        if not await self._is_admin(message):
            await message.reply("🧠 Стирать память темы может только администрация.")
            return
        topic_id = self._topic_id(message)
        removed = await self.storage.clear_scope(message.chat.id, topic_id)
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

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
from .memory import classify_memory_event
from .models import (
    BehaviorMode,
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
        self._active_topics: dict[tuple[int, int], Any] = {}

    def is_allowed_chat(self, chat_id: int) -> bool:
        return int(chat_id) in self.chat_ids

    @staticmethod
    def _chat_type_value(message: Message) -> str:
        chat_type = getattr(message.chat, "type", "")
        return str(getattr(chat_type, "value", chat_type))

    @staticmethod
    def _topic_id(message: Message) -> int:
        return normalize_topic_id(getattr(message, "message_thread_id", None))

    def remember_active_topic(self, message: Message) -> None:
        """Remember only scopes observed in this process for bounded supervisor scans."""
        chat_id = int(message.chat.id)
        if not self.is_allowed_chat(chat_id):
            return
        self._active_topics[(chat_id, self._topic_id(message))] = message

    def is_eligible_learning_message(self, message: Message) -> bool:
        """Return whether a message belongs to the legacy text/activity path."""
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

    def _is_eligible_memory_sender(self, message: Message) -> bool:
        if not self.is_allowed_chat(message.chat.id):
            return False
        if self._chat_type_value(message) not in {"group", "supergroup"}:
            return False
        from_user = getattr(message, "from_user", None)
        return from_user is not None and not bool(getattr(from_user, "is_bot", False))

    @staticmethod
    def _event_looks_like_command(event: object) -> bool:
        for name in ("text", "caption"):
            value = getattr(event, name, None)
            if isinstance(value, str) and value.lstrip().startswith("/"):
                return True
        return False

    async def _is_admin_identity(self, chat_id: int, user_id: int | None) -> bool:
        if user_id is None:
            return False
        try:
            member = await self.app.bot.get_chat_member(chat_id=int(chat_id), user_id=int(user_id))
        except Exception:
            LOGGER.info(
                "Could not check entertainment admin status chat_id=%s user_id=%s",
                chat_id,
                user_id,
                exc_info=True,
            )
            return False
        return getattr(member, "status", None) in {"creator", "administrator"}

    async def _is_admin(self, message: Message) -> bool:
        from_user = getattr(message, "from_user", None)
        return await self._is_admin_identity(
            int(message.chat.id),
            getattr(from_user, "id", None),
        )

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

    async def evaluate_topic(
        self,
        message: Message,
        *,
        supervisor: bool = False,
    ) -> EntertainmentActionRecord | None:
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
        if supervisor and phase not in {ConversationPhase.QUIET, ConversationPhase.COOLDOWN}:
            return None

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
                since=int(last_action.created_at) + 1,
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

        escaped = html.escape(generated)
        if supervisor:
            await self.app.bot.send_message(
                chat_id=chat_id,
                text=escaped,
                message_thread_id=(topic_id or None),
            )
        else:
            await message.reply(escaped)

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
                "source": "supervisor" if supervisor else "message",
            },
        )
        action_id = await self.storage.record_action(record)
        stored_record = replace(record, id=action_id)
        LOGGER.info(
            "ENTERTAINMENT_AUTONOMOUS_ACTION chat_id=%s topic_id=%s action=%s phase=%s mode=%s memory=%s source=%s",
            chat_id,
            topic_id,
            selected.action_type.value,
            phase.value,
            settings.behavior_mode.value,
            memory_count,
            record.metadata["source"],
        )
        return stored_record

    async def run_supervisor_tick(self) -> None:
        """Evaluate only recently active in-process scopes; never scan full history."""
        now = int(self._now_fn())
        for key, message in list(self._active_topics.items()):
            chat_id, topic_id = key
            try:
                recent_human = await self.storage.human_messages_since(
                    chat_id,
                    topic_id,
                    since=now - 30 * 60,
                )
                if recent_human <= 0:
                    self._active_topics.pop(key, None)
                    continue
                await self.evaluate_topic(message, supervisor=True)
            except Exception:
                LOGGER.exception(
                    "Entertainment supervisor scope failed chat_id=%s topic_id=%s",
                    chat_id,
                    topic_id,
                )

    async def _observe_legacy_only(self, message: Message) -> None:
        """One-release fallback for consumers still constructing core storage directly."""
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
        self.remember_active_topic(message)
        await self.evaluate_topic(message)

    async def observe_message(self, message: Message) -> None:
        if not self._is_eligible_memory_sender(message):
            return

        add_event = getattr(self.storage, "add_event", None)
        get_remember_enabled = getattr(self.storage, "get_remember_enabled", None)
        if not callable(add_event) or not callable(get_remember_enabled):
            await self._observe_legacy_only(message)
            return

        settings = await self.storage.get_settings(message.chat.id)
        if not settings.enabled:
            return

        user_id = int(message.from_user.id)
        if not await get_remember_enabled(int(message.chat.id), user_id):
            return

        topic_id = self._topic_id(message)
        now = int(self._now_fn())
        event = classify_memory_event(
            message,
            chat_id=int(message.chat.id),
            topic_id=topic_id,
            created_at=now,
        )
        if event is None or self._event_looks_like_command(event):
            return

        await add_event(event)

        # Phase A keeps autonomy/activity on the legacy text gate. Media enriches
        # Culture Memory without increasing current activity or response cadence.
        if not self.is_eligible_learning_message(message):
            return

        text = message.text.strip()
        await self.storage.add_message(
            chat_id=message.chat.id,
            topic_id=topic_id,
            user_id=user_id,
            text=text,
            message_id=getattr(message, "message_id", None),
            created_at=now,
        )
        self.remember_active_topic(message)
        await self.evaluate_topic(message)

    async def set_remember_me(self, message: Message, enabled: bool) -> None:
        from_user = getattr(message, "from_user", None)
        user_id = getattr(from_user, "id", None)
        if user_id is None:
            return
        await self.storage.set_remember_enabled(int(message.chat.id), int(user_id), bool(enabled))
        if enabled:
            await message.reply("🧠 Снова запоминаю твои новые сообщения в этом чате.")
        else:
            await message.reply(
                "🧠 Хорошо, теперь я не запоминаю твои новые сообщения в этом чате. "
                "Старую память можно удалить командой /fun_delete_me."
            )

    async def delete_my_memory(self, message: Message) -> int:
        from_user = getattr(message, "from_user", None)
        user_id = getattr(from_user, "id", None)
        if user_id is None:
            return 0
        chat_id = int(message.chat.id)
        canonical = await self.storage.delete_user_memory(chat_id, int(user_id))
        legacy = await self.storage.delete_legacy_user_messages(chat_id, int(user_id))
        for key in [key for key in self._active_topics if key[0] == chat_id]:
            self._active_topics.pop(key, None)
        removed = int(canonical) + int(legacy)
        await message.reply(
            f"🧠 Удалил из своей памяти: <b>{removed}</b> записей. "
            "Сообщения в Telegram не удалялись."
        )
        return removed

    @staticmethod
    def panel_keyboard(settings: EntertainmentSettings | None = None) -> InlineKeyboardMarkup:
        settings = settings or EntertainmentSettings()
        mode_specs = (
            (BehaviorMode.CALM, "🌙 Спокойный"),
            (BehaviorMode.ALIVE, "✨ Живой"),
            (BehaviorMode.ACTIVE, "⚡ Активный"),
        )
        mode_row = [
            InlineKeyboardButton(
                text=(f"✅ {label}" if settings.behavior_mode is mode else label),
                callback_data=f"fun:mode:{mode.value}",
            )
            for mode, label in mode_specs
        ]
        toggle = InlineKeyboardButton(
            text="⏸ Выключить" if settings.enabled else "▶️ Включить",
            callback_data="fun:disable" if settings.enabled else "fun:enable",
        )
        return InlineKeyboardMarkup(
            inline_keyboard=[
                mode_row,
                [InlineKeyboardButton(text="🧠 Память", callback_data="fun:status"), toggle],
                [InlineKeyboardButton(text="🎲 Сгенерировать реплику", callback_data="fun:generate")],
            ]
        )

    async def show_panel(self, message: Message) -> None:
        settings = await self.storage.get_settings(message.chat.id)
        topic_id = self._topic_id(message)
        memory_counts = getattr(self.storage, "memory_counts", None)
        if callable(memory_counts):
            counts = await memory_counts(message.chat.id, topic_id)
            count = counts.total
            culture_line = (
                f"Текст: <b>{counts.text}</b> · Emoji: <b>{counts.emoji}</b> · "
                f"Стикеры: <b>{counts.sticker}</b> · "
                f"Фото/анимации: <b>{counts.photo + counts.animation}</b>\n"
            )
        else:
            count = await self.storage.message_count(message.chat.id, topic_id)
            culture_line = ""
        state = "включен" if settings.enabled else "выключен"
        await message.reply(
            "🎭 <b>Развлекательный режим</b>\n\n"
            f"Состояние: <b>{state}</b>\n"
            f"Режим поведения: <b>{settings.behavior_mode.display_name}</b>\n"
            f"Память этой темы: <b>{count}</b>/{MEMORY_LIMIT}\n"
            f"{culture_line}\n"
            "Бот сам выбирает момент по активности конкретной темы, хранит историю своих действий "
            "и не должен перебивать живой разговор.\n"
            "Темы форума и разные чаты изолированы друг от друга.\n\n"
            "Память: /fun_ignore_me · /fun_remember_me · /fun_delete_me\n"
            "Админам: выберите режим кнопкой ниже · /fun_on · /fun_off · /fun_forget",
            reply_markup=self.panel_keyboard(settings),
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

    async def set_behavior_mode(self, message: Message, mode: BehaviorMode) -> None:
        if not await self._is_admin(message):
            await message.reply("⚙️ Режим поведения может менять только администрация чата.")
            return
        current = await self.storage.get_settings(message.chat.id)
        await self.storage.save_settings(message.chat.id, replace(current, behavior_mode=mode))
        await message.reply(f"🎭 Режим поведения: <b>{mode.display_name}</b>.")

    async def set_laziness(self, message: Message) -> None:
        if not await self._is_admin(message):
            await message.reply("⚙️ Настройки поведения может менять только администрация чата.")
            return
        await message.reply(
            "⚙️ Числовая настройка больше не управляет поведением. "
            "Открой /fun и выбери режим: Спокойный, Живой или Активный."
        )

    async def set_cooldown(self, message: Message) -> None:
        if not await self._is_admin(message):
            await message.reply("⚙️ Настройки поведения может менять только администрация чата.")
            return
        await message.reply(
            "⚙️ Ручная задержка больше не управляет автономным движком. "
            "Открой /fun и выбери режим поведения."
        )

    async def forget_chat(self, message: Message) -> None:
        if not await self._is_admin(message):
            await message.reply("🧠 Стирать память темы может только администрация.")
            return
        chat_id = int(message.chat.id)
        topic_id = self._topic_id(message)
        clear_memory_scope = getattr(self.storage, "clear_memory_scope", None)
        canonical_removed = (
            await clear_memory_scope(chat_id, topic_id)
            if callable(clear_memory_scope)
            else 0
        )
        legacy_removed = await self.storage.clear_scope(chat_id, topic_id)
        self._active_topics.pop((chat_id, topic_id), None)
        await message.reply(
            f"🧠 Память этой темы очищена. Удалено записей: <b>{canonical_removed + legacy_removed}</b>. "
            "Другие темы и чаты не затронуты."
        )

    async def _handle_mode_callback(self, callback: CallbackQuery, mode: BehaviorMode) -> None:
        assert callback.message is not None
        chat_id = int(callback.message.chat.id)
        actor_id = getattr(getattr(callback, "from_user", None), "id", None)
        if not await self._is_admin_identity(chat_id, actor_id):
            await callback.answer("Режим может менять только администрация чата.", show_alert=True)
            return
        current = await self.storage.get_settings(chat_id)
        await self.storage.save_settings(chat_id, replace(current, behavior_mode=mode))
        await callback.answer(f"Режим: {mode.display_name}")
        await self.show_panel(callback.message)  # type: ignore[arg-type]

    async def _handle_enabled_callback(self, callback: CallbackQuery, enabled: bool) -> None:
        assert callback.message is not None
        chat_id = int(callback.message.chat.id)
        actor_id = getattr(getattr(callback, "from_user", None), "id", None)
        if not await self._is_admin_identity(chat_id, actor_id):
            await callback.answer("Эту настройку может менять только администрация чата.", show_alert=True)
            return
        current = await self.storage.get_settings(chat_id)
        await self.storage.save_settings(chat_id, replace(current, enabled=enabled))
        await callback.answer("Включено" if enabled else "Выключено")
        await self.show_panel(callback.message)  # type: ignore[arg-type]

    async def handle_callback(self, callback: CallbackQuery) -> None:
        if callback.message is None:
            await callback.answer()
            return
        chat = getattr(callback.message, "chat", None)
        if chat is None or not self.is_allowed_chat(chat.id):
            await callback.answer("Здесь развлекательный режим недоступен.", show_alert=True)
            return
        data = callback.data or ""
        if data.startswith("fun:mode:"):
            try:
                mode = BehaviorMode(data.rsplit(":", 1)[1])
            except ValueError:
                await callback.answer("Неизвестный режим.", show_alert=True)
                return
            await self._handle_mode_callback(callback, mode)
            return
        if data == "fun:enable":
            await self._handle_enabled_callback(callback, True)
            return
        if data == "fun:disable":
            await self._handle_enabled_callback(callback, False)
            return
        if data == "fun:generate":
            await callback.answer("Собираю фразу…")
            await self.generate_now(callback.message)  # type: ignore[arg-type]
            return
        if data == "fun:status":
            await callback.answer()
            await self.show_status(callback.message)  # type: ignore[arg-type]
            return
        await callback.answer()

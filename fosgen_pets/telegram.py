"""Private permanent-pet UI and conservative post-moderation counting.

Only the successful legacy text collector can qualify a group message. Raw text
is transient input to the store's HMAC filter and is never logged here.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape
import logging
import re
from typing import Any
from zoneinfo import ZoneInfo

from aiogram import BaseMiddleware, F
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message,
)

from .core import MessageEligibility, UNLOCK_MESSAGES, eligible_message

logger = logging.getLogger(__name__)
COMMANDS = ("pet", "pet_start", "pet_progress", "pet_pause", "pet_privacy", "pet_passport", "pet_history", "pet_story")
CALLBACK = re.compile(r"(petcare|petview):([1-9][0-9]{0,18}):(feed|play|sleep|passport|history|story)\Z")
UNAVAILABLE = "Питомцы временно недоступны. Попробуй позже: прогресс и питомец сохраняются."
PRIVACY = (
    "🐾 Для питомца тексты сообщений не сохраняются. Учет хранит ID пользователя, чата и сообщения, "
    "время, счетчик и HMAC отпечатки для защиты от повторов. Учитываются подходящие сообщения "
    "только после согласия и прохождения модерации в подключенных чатах. "
    "Команды, пересылки и сообщения ботов не учитываются.\n"
    "/pet_pause останавливает учет. Уже накопленный прогресс и питомец сохраняются. "
    "Питомец один навсегда; его внешний вид не заменяется."
)


def _private_owner(message: Message, user: Any = None) -> int:
    user = user or message.from_user
    if (message.chat.type != "private" or user is None or user.is_bot
            or user.id != message.chat.id or message.sender_chat is not None):
        raise SkipHandler
    return user.id


class PetCountingMiddleware(BaseMiddleware):
    """Inner middleware: inspect the selected handler, then count after success."""

    def __init__(self, app: Any, store: Any, chat_ids: frozenset[int]):
        self.app, self.store, self.chat_ids = app, store, chat_ids

    async def __call__(self, handler, event: Message, data: dict[str, Any]):
        callback = getattr(data.get("handler"), "callback", None)
        user = event.from_user
        pending = user is not None and user.id in getattr(self.app, "pending_users", {})
        candidate = None
        if (getattr(callback, "__name__", "") == "collect_text_messages"
                and callback is getattr(self.app, "collect_text_messages", callback)
                and not pending and event.chat.type in ("group", "supergroup")
                and event.chat.id in self.chat_ids
                and event.chat.id in getattr(self.app, "ALLOWED_CHATS", ())
                and event.chat.id not in getattr(self.app, "LEXICON_ONLY_CHAT_IDS", ())
                and user is not None and not user.is_bot and event.sender_chat is None
                and event.edit_date is None and event.forward_origin is None and event.via_bot is None
                and event.date <= datetime.now(timezone.utc) + timedelta(minutes=1)):
            proposed = MessageEligibility(user.id, event.chat.id, event.message_id,
                                          event.text, opted_in=True)
            if eligible_message(proposed):
                candidate = proposed
        # Exceptions and SkipHandler from the moderation/legacy path propagate.
        result = await handler(event, data)
        if candidate is not None:
            try:
                await self.store.record_message(candidate, now=event.date)
            except Exception as exc:
                # Exception messages can contain text, credentials or SQL arguments.
                logger.warning("FOSGEN_PETS_COUNT_FAILED reason=%s", type(exc).__name__)
        return result


def _keyboard(pet_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=f"petcare:{pet_id}:{action}")
         for label, action in (("🥕 Покормить", "feed"), ("🎾 Поиграть", "play"), ("💤 Уложить спать", "sleep"))],
        [InlineKeyboardButton(text=label, callback_data=f"petview:{pet_id}:{action}")
         for label, action in (("🧬 Паспорт", "passport"), ("📖 История", "history"))],
        [InlineKeyboardButton(text="📜 История появления", callback_data=f"petview:{pet_id}:story")],
        [InlineKeyboardButton(text="⚪ Шашки", callback_data=f"petdraughts:{pet_id}:open")],
    ])


def _status(pet: Any) -> str:
    mood = {"calm": "спокойное", "content": "довольное", "playful": "игривое", "rested": "отдохнувшее"}.get(pet.mood, "спокойное")
    name = (pet.name or "Твой питомец").replace("ё", "е").replace("Ё", "Е")
    return (f"🐾 {name} · #{pet.id}\n"
            f"Настроение: {mood}\nЭнергия: {pet.energy}/100\nСытость: {pet.satiety}/100\n"
            "Один навсегда. Не умирает и не теряется без ухода.")


class PetTelegramService:
    def __init__(self, app: Any, store: Any, chat_ids: frozenset[int]):
        self.app, self.store = app, store
        self.counting = PetCountingMiddleware(app, store, chat_ids)
        self._registered: list[tuple[Any, Any]] = []

    def promote_handlers(self) -> None:
        """Move only this service's exact callbacks before legacy catch-alls."""
        for observer, callback in self._registered:
            selected = [item for item in observer.handlers if item.callback is callback]
            observer.handlers[:] = selected + [item for item in observer.handlers if item.callback is not callback]

    async def _progress(self, message: Message, owner: int) -> None:
        progress = await self.store.progress(owner)
        if not progress.opted_in:
            text = (f"🐾 Прогресс: {progress.count}/{UNLOCK_MESSAGES}. Учет остановлен.\n"
                    "/pet_start — дать согласие на учет новых сообщений. /pet_privacy — данные и правила.")
        elif progress.count >= UNLOCK_MESSAGES:
            text = "🐾 Все 100 сообщений набраны! Открой /pet: питомец появится один раз и останется навсегда."
        else:
            text = (f"🐾 Прогресс: {progress.count}/{UNLOCK_MESSAGES}. "
                    "Учитываются подходящие сообщения после согласия в подключенных чатах.\n"
                    "/pet_pause — остановить учет; /pet_privacy — данные и правила.")
        await message.answer(text, parse_mode=None)

    async def _show(self, message: Message, owner: int) -> None:
        pet = await self.store.get(owner)
        if pet is None:
            progress = await self.store.progress(owner)
            if progress.opted_in and progress.count >= UNLOCK_MESSAGES:
                pet = await self.store.adopt(owner)
        if pet is None:
            await self._progress(message, owner)
        elif pet.png:
            await message.answer_photo(BufferedInputFile(bytes(pet.png), filename=f"pet_{pet.id}.png"),
                                       caption=_status(pet), reply_markup=_keyboard(pet.id), parse_mode=None)
        else:
            await message.answer(_status(pet) + "\nИсходное изображение пока недоступно. "
                                 "Питомец сохранен: новое изображение не создается.",
                                 reply_markup=_keyboard(pet.id), parse_mode=None)

    async def _passport(self, message: Message, pet: Any) -> None:
        appearance = pet.appearance
        seed = appearance.pattern_seed if appearance.pattern_seed is not None else "нет (v1)"
        await message.answer(
            f"🧬 Паспорт питомца #{pet.id}\nВерсия внешности: {appearance.asset_version}\n"
            f"Seed: {seed}\nDNA SHA256: {escape(appearance.fingerprint())}\n"
            f"PNG pixel SHA256: {escape(pet.pixel_hash or 'нет')}\n"
            f"Каноническая DNA:\n<code>{escape(appearance.canonical_dna())}</code>\n"
            "Внешность и исходное изображение закреплены навсегда.", parse_mode="HTML")

    async def _history(self, message: Message, owner: int) -> None:
        events = await self.store.history(owner, limit=10)
        labels = {"adopted": "Питомец появился", "care:feed": "Питомца покормили",
                  "care:play": "С питомцем поиграли", "care:sleep": "Питомец отдохнул"}
        lines = ["📖 История питомца:"]
        for event in events:
            when = event.get("created_at")
            stamp = when.astimezone(ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y %H:%M МСК") if isinstance(when, datetime) else ""
            lines.append(f"{stamp} · {labels.get(event.get('event_type'), 'Событие питомца')}")
        await message.answer("\n".join(lines) if events else "История питомца пока пуста.", parse_mode=None)

    async def _story(self, message: Message, pet: Any) -> None:
        story = (pet.origin_story or "Этот питомец нашел свой дом и верного друга.").replace("ё", "е").replace("Ё", "Е")
        await message.answer(f"📜 История появления питомца #{pet.id}\n{story}", parse_mode=None)

    async def command(self, message: Message, command: CommandObject) -> None:
        owner = _private_owner(message)
        try:
            if command.command == "pet_privacy":
                await message.answer(PRIVACY, parse_mode=None)
            elif command.command == "pet_start":
                await self.store.opt_in(owner)
                await message.answer(
                    "🐾 Согласие принято. После согласия нужно 100 подходящих сообщений в подключенных чатах. "
                    "Команды, пересылки, повторы и сообщения ботов не считаются.\n"
                    "У тебя будет один питомец навсегда: он не умирает, не теряется без ухода, "
                    "его внешность не заменяется. /pet_progress — прогресс; /pet — питомец.\n"
                    "/pet_pause — остановить учет с сохранением прогресса и питомца; /pet_privacy — данные.",
                    parse_mode=None)
            elif command.command == "pet_pause":
                await self.store.revoke(owner)
                await message.answer("Учет остановлен. Прогресс и питомец сохранены. Возобновить: /pet_start.", parse_mode=None)
            elif command.command == "pet_progress":
                await self._progress(message, owner)
            elif command.command == "pet":
                await self._show(message, owner)
            else:
                pet = await self.store.get(owner)
                if pet is None:
                    await self._progress(message, owner)
                elif command.command == "pet_passport":
                    await self._passport(message, pet)
                elif command.command == "pet_history":
                    await self._history(message, owner)
                elif command.command == "pet_story":
                    await self._story(message, pet)
        except Exception as exc:
            logger.warning("FOSGEN_PETS_COMMAND_FAILED reason=%s", type(exc).__name__)
            await message.answer(UNAVAILABLE, parse_mode=None)

    async def callback(self, callback: CallbackQuery) -> None:
        if not isinstance(callback.message, Message):
            raise SkipHandler
        owner = _private_owner(callback.message, callback.from_user)
        match = CALLBACK.fullmatch(callback.data or "")
        if match is None:
            raise SkipHandler
        kind, pet_id, action = match.groups()
        if int(pet_id) > 2**63 - 1 or (kind == "petcare") != (action in ("feed", "play", "sleep")):
            raise SkipHandler
        await callback.answer()
        try:
            # Payload selects a pet, never an owner. All reads use the sender ID.
            pet = await self.store.get(owner)
            if pet is None or pet.id != int(pet_id):
                await callback.message.answer("Эта кнопка устарела или относится к другому питомцу. Открой /pet.", parse_mode=None)
            elif kind == "petview":
                if action == "passport":
                    await self._passport(callback.message, pet)
                elif action == "history":
                    await self._history(callback.message, owner)
                else:
                    await self._story(callback.message, pet)
            else:
                result = await self.store.care(owner, action, callback.id)
                if result.pet is None:
                    text = "Питомец пока недоступен. Открой /pet позже."
                elif result.reason == "cooldown":
                    text = f"Нужна небольшая пауза: {max(1, result.retry_after)} сек.\n" + _status(result.pet)
                elif result.reason == "duplicate":
                    text = "Это действие уже учтено.\n" + _status(result.pet)
                else:
                    text = {"feed": "Питомец поел.", "play": "Вы поиграли!", "sleep": "Питомец отдохнул."}[action] + "\n" + _status(result.pet)
                await callback.message.answer(text, reply_markup=_keyboard(pet.id), parse_mode=None)
        except Exception as exc:
            logger.warning("FOSGEN_PETS_CARE_FAILED reason=%s", type(exc).__name__)
            await callback.message.answer(UNAVAILABLE, parse_mode=None)


def register_pet_handlers(app: Any, store: Any, *, chat_ids: frozenset[int]) -> PetTelegramService:
    service = PetTelegramService(app, store, chat_ids)
    # Bound-method access creates fresh objects: retain the registered identity.
    command, callback = service.command, service.callback
    app.dp.message.register(command, Command(*COMMANDS))
    app.dp.callback_query.register(callback, F.data.startswith("petcare:") | F.data.startswith("petview:"))
    service._registered = [(app.dp.message, command), (app.dp.callback_query, callback)]
    from .draughts_telegram import register_draughts_handlers
    service.draughts = register_draughts_handlers(app, store)
    service._registered.extend(service.draughts._registered)
    app.dp.message.middleware(service.counting)
    service.promote_handlers()
    return service

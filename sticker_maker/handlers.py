"""DM-only sticker creation and user-owned, bot-managed sticker packs."""

from __future__ import annotations

import logging
import secrets
import tempfile
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiogram import F
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import BaseFilter, Command
from aiogram.types import (
    BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup,
    CallbackQuery, InputSticker, Message,
)

from .converter import MAX_SOURCE_BYTES, StickerConversionError, convert_to_sticker

LOGGER = logging.getLogger(__name__)
PENDING_TTL_SECONDS = 300
COOLDOWN_SECONDS = 20
MAX_CONVERSIONS = 2
MAX_PACK_PARTS = 5
MAX_PACK_STICKERS = 120
RESULT_TTL_SECONDS = 600
MAX_PENDING_RESULTS = 64
CALLBACK_PREFIX = "sticker:"


@dataclass
class ConvertedSticker:
    user_id: int
    webm: bytes
    expires_at: float
    file_sent: bool = False
    pack: str | None = None
    busy: bool = False


def choice_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="📁 Получить файл .webm", callback_data=f"sticker:file:{token}",
        )],
        [InlineKeyboardButton(
            text="➕ Добавить в набор Fosgen", callback_data=f"sticker:pack:{token}",
        )],
    ])



def private(message: Message) -> bool:
    return message.chat.type == "private" and message.from_user is not None and not message.from_user.is_bot


def media_file(message: Message) -> Any | None:
    """GIF documents and Telegram's MP4-backed GIF animations only."""
    if message.animation is not None:
        return message.animation
    doc = message.document
    if doc is not None and (
        (doc.mime_type or "").lower() == "image/gif"
        or (doc.file_name or "").lower().endswith(".gif")
    ):
        return doc
    return None


def pack_name(user_id: int, username: str, part: int = 1) -> str:
    """Deterministic, user-scoped, valid Telegram short name."""
    suffix = "" if part == 1 else f"_{part}"
    result = f"fosgen_{user_id}{suffix}_by_{username}"
    if len(result) > 64:
        raise ValueError("Bot username is too long for sticker set name")
    return result


def sticker_item(webm: bytes) -> InputSticker:
    return InputSticker(
        sticker=BufferedInputFile(webm, filename="fosgen.webm"),
        format="video", emoji_list=["✨"],
    )


def _set_missing(exc: TelegramBadRequest) -> bool:
    msg = str(exc).lower()
    return "stickerset_invalid" in msg or "not found" in msg


def _set_full(exc: TelegramBadRequest) -> bool:
    msg = str(exc).lower()
    return "too much" in msg or "too many" in msg or "full" in msg


async def save_to_personal_pack(bot: Any, user_id: int, webm: bytes) -> str:
    """Never modify unrelated packs; only the deterministic set made by this bot."""
    me = await bot.get_me()
    if not me.username:
        raise RuntimeError("Bot has no username")
    for part in range(1, MAX_PACK_PARTS + 1):
        name = pack_name(user_id, me.username, part)
        try:
            existing = await bot.get_sticker_set(name=name)
        except TelegramBadRequest as exc:
            if not _set_missing(exc):
                raise
            await bot.create_new_sticker_set(
                user_id=user_id,
                name=name,
                title=f"GIF-стикеры Fosgen • {part}",
                stickers=[sticker_item(webm)],
                sticker_type="regular",
            )
            return name
        if len(existing.stickers) >= MAX_PACK_STICKERS:
            continue
        try:
            await bot.add_sticker_to_set(
                user_id=user_id, name=name, sticker=sticker_item(webm),
            )
            return name
        except TelegramBadRequest as exc:
            if _set_full(exc):
                continue
            raise
    raise StickerConversionError("Твои наборы переполнены. Напиши администратору бота.")


class StickerState:
    def __init__(self) -> None:
        self.pending: dict[int, float] = {}
        self.active: set[int] = set()
        self.recent: OrderedDict[int, float] = OrderedDict()
        self.results: OrderedDict[str, ConvertedSticker] = OrderedDict()

    def waiting(self, user_id: int) -> bool:
        expires = self.pending.get(user_id, 0)
        if expires <= time.monotonic():
            self.pending.pop(user_id, None)
            return False
        return True

    def expect(self, user_id: int) -> None:
        now = time.monotonic()
        if len(self.pending) >= 2048:
            self.pending = {key: expiry for key, expiry in self.pending.items() if expiry > now}
        if len(self.pending) >= 2048 and user_id not in self.pending:
            self.pending.pop(min(self.pending, key=self.pending.get))
        self.pending[user_id] = now + PENDING_TTL_SECONDS

    def start(self, user_id: int) -> str | None:
        now = time.monotonic()
        if user_id in self.active:
            return "Уже обрабатываю твою GIF."
        if len(self.active) >= MAX_CONVERSIONS:
            return "Сейчас конвертер занят. Попробуй чуть позже."
        if now - self.recent.get(user_id, -COOLDOWN_SECONDS) < COOLDOWN_SECONDS:
            return "Подожди 20 секунд между конвертациями."
        self.active.add(user_id)
        self.recent[user_id] = now
        self.recent.move_to_end(user_id)
        if len(self.recent) > 4096:
            self.recent.popitem(last=False)
        self.pending.pop(user_id, None)
        return None

    def finish(self, user_id: int) -> None:
        self.active.discard(user_id)

    def remember(self, user_id: int, webm: bytes) -> str:
        now = time.monotonic()
        for token, item in list(self.results.items()):
            if item.expires_at <= now or item.user_id == user_id:
                self.results.pop(token, None)
        while len(self.results) >= MAX_PENDING_RESULTS:
            self.results.popitem(last=False)
        token = secrets.token_urlsafe(12)
        self.results[token] = ConvertedSticker(user_id, webm, now + RESULT_TTL_SECONDS)
        return token

    def get_result(self, token: str, user_id: int) -> ConvertedSticker | None:
        item = self.results.get(token)
        if item is None or item.user_id != user_id:
            return None
        if item.expires_at <= time.monotonic():
            self.results.pop(token, None)
            return None
        return item


class WaitingForSticker(BaseFilter):
    def __init__(self, state: StickerState) -> None:
        self.state = state

    async def __call__(self, message: Message) -> bool:
        return bool(private(message) and self.state.waiting(message.from_user.id))


async def create_sticker(
    message: Message, state: StickerState, *, source_message: Message | None = None,
) -> None:
    if not private(message):
        return
    source = media_file(source_message or message)
    if source is None:
        await message.answer("Отправь GIF-анимацию (обычную или файлом .gif).")
        return
    if source.file_size is None or source.file_size > MAX_SOURCE_BYTES:
        await message.answer("GIF должна быть не больше 12 МБ.")
        return

    user_id = message.from_user.id
    reason = state.start(user_id)
    if reason is not None:
        await message.answer(reason)
        return
    try:
        status = await message.answer("🎞 Превращаю GIF в стикер...")
        with tempfile.TemporaryDirectory(prefix="fosgen_sticker_") as folder:
            input_path = Path(folder) / "source"
            output_path = Path(folder) / "sticker.webm"
            file = await message.bot.get_file(source.file_id)
            if not file.file_path or (file.file_size and file.file_size > MAX_SOURCE_BYTES):
                raise StickerConversionError("GIF слишком большая или недоступна для скачивания.")
            await message.bot.download_file(file.file_path, destination=input_path, timeout=30)
            webm = await convert_to_sticker(input_path, output_path)

        token = state.remember(user_id, webm)
        await status.edit_text(
            "✅ GIF готова. Выбери, что сделать:\n\n"
            "📁 Получить .webm, чтобы загрузить его в свой существующий набор через @Stickers.\n"
            "➕ Добавить в персональный набор Fosgen.\n\n"
            "Обе кнопки доступны 10 минут. До выбора набор не меняется.",
            reply_markup=choice_keyboard(token),
        )
    except StickerConversionError as exc:
        await message.answer(f"Не получилось: {exc}")
    except TelegramAPIError:
        LOGGER.exception("STICKER_TELEGRAM_ERROR user_id=%s", user_id)
        await message.answer("Не удалось обработать GIF через Telegram. Попробуй позже.")
    except Exception:
        LOGGER.exception("STICKER_UNEXPECTED_ERROR user_id=%s", user_id)
        await message.answer("Временная ошибка конвертера. Попробуй другую GIF позже.")
    finally:
        state.finish(user_id)



async def handle_sticker_choice(callback: CallbackQuery, state: StickerState) -> None:
    """Handle file/export and pack actions without touching other sticker sets."""
    message = callback.message
    if message is None or getattr(getattr(message, "chat", None), "type", None) != "private":
        await callback.answer("Только в личке с ботом.", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    if len(parts) != 3 or parts[0] != "sticker" or parts[1] not in ("file", "pack"):
        await callback.answer("Неизвестное действие.", show_alert=True)
        return
    action, token = parts[1:]
    result = state.get_result(token, callback.from_user.id)
    if result is None:
        await callback.answer("Результат устарел. Повтори /sticker.", show_alert=True)
        return
    if result.busy:
        await callback.answer("Предыдущий запрос еще выполняется.", show_alert=True)
        return
    if action == "file" and result.file_sent:
        await callback.answer("Файл уже отправлен выше.", show_alert=True)
        return
    result.busy = True
    await callback.answer()
    try:
        if action == "file":
            await callback.bot.send_document(
                chat_id=callback.from_user.id,
                document=BufferedInputFile(result.webm, filename="fosgen_sticker.webm"),
                caption=(
                    "📁 Видеостикер .webm готов!\n\n"
                    "Открой @Stickers, выбери /addsticker и свой существующий набор. "
                    "Отправь полученный файл в виде документа и назначь эмодзи по инструкции."
                ),
            )
            result.file_sent = True
        else:
            if result.pack is None:
                # Only bot-created, user-scoped packs can be managed by Bot API.
                result.pack = await save_to_personal_pack(
                    callback.bot, callback.from_user.id, result.webm,
                )
            await callback.bot.send_sticker(
                chat_id=callback.from_user.id,
                sticker=BufferedInputFile(result.webm, filename="fosgen.webm"),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(
                        text="➕ Открыть набор Fosgen",
                        url=f"https://t.me/addstickers/{result.pack}",
                    ),
                ]]),
            )
    except StickerConversionError as exc:
        await callback.bot.send_message(
            chat_id=callback.from_user.id, text=f"Не получилось: {exc}",
        )
    except TelegramAPIError:
        LOGGER.exception("STICKER_CHOICE_TELEGRAM_FAILED action=%s user_id=%s", action, callback.from_user.id)
        await callback.bot.send_message(
            chat_id=callback.from_user.id,
            text="Telegram не выполнил операцию. Попробуй нажать кнопку еще раз.",
        )
    except Exception:
        LOGGER.exception("STICKER_CHOICE_FAILED action=%s user_id=%s", action, callback.from_user.id)
        await callback.bot.send_message(
            chat_id=callback.from_user.id, text="Временная ошибка. Попробуй снова.",
        )
    finally:
        result.busy = False


def register_sticker_handlers(app: Any) -> StickerState:
    """Register only DM conversion handlers and private callback choices."""
    state = StickerState()
    dp = app.dp

    @dp.message(F.chat.type == "private", Command("sticker"))
    async def sticker_command(message: Message) -> None:
        if not private(message):
            return
        replied = message.reply_to_message
        if replied is not None and media_file(replied) is not None:
            await create_sticker(message, state, source_message=replied)
            return
        state.expect(message.from_user.id)
        await message.answer(
            "🎞 Пришли GIF сюда, и я превращу ее в видеостикер. "
            "После конвертации выберешь, куда сохранить результат.\n"
            "Лимит: 12 МБ, первые 3 секунды GIF. "
            "Можно также ответить командой /sticker на уже отправленную GIF."
        )

    @dp.message(WaitingForSticker(state), F.animation | F.document)
    async def sticker_upload(message: Message) -> None:
        await create_sticker(message, state)

    @dp.callback_query(F.data.startswith(CALLBACK_PREFIX))
    async def sticker_choice(callback: CallbackQuery) -> None:
        await handle_sticker_choice(callback, state)

    for i, handler in enumerate(dp.callback_query.handlers):
        if handler.callback is sticker_choice:
            dp.callback_query.handlers.insert(0, dp.callback_query.handlers.pop(i))
            break

    # Legacy message collector catches all F.text (even DMs). Put our command
    # above it, but keep group moderation and Lexicon guards unaffected.
    handlers = dp.message.handlers
    for handler in (sticker_upload, sticker_command):
        for i, item in enumerate(handlers):
            if item.callback is handler:
                handlers.insert(0, handlers.pop(i))
                break
    return state

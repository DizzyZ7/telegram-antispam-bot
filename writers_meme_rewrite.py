"""Scoped, non-punitive meme removal and harmless replacement."""
from __future__ import annotations

import logging
from collections import OrderedDict
from typing import Any

from aiogram import BaseMiddleware
from aiogram.filters import Command
from aiogram.types import LinkPreviewOptions, Message

from entertainment.moderation_safety import is_safe_entertainment_source
from writers_meme_policy import contains_meme_words, rewrite_meme_words
from writers_meme_storage import MemeStickerStore
from writers_moderation import WRITERS_PROFANITY_EXEMPT_TOPIC_IDS, contains_prohibited_language

LOGGER = logging.getLogger(__name__)


def _text_parts(text: str, limit: int = 4096) -> list[str]:
    """Bound Telegram text in UTF-16 units without splitting Unicode characters."""
    parts, start, size = [], 0, 0
    for index, char in enumerate(text):
        units = 2 if ord(char) > 0xFFFF else 1
        if size + units > limit:
            parts.append(text[start:index])
            start, size = index, 0
        size += units
    if start < len(text):
        parts.append(text[start:])
    return parts


class WritersMemeMiddleware(BaseMiddleware):
    def __init__(self, app: Any, scope: Any, store: MemeStickerStore,
                 on_message_deleted: Any = None) -> None:
        self.app, self.scope, self.store = app, scope, store
        self.on_message_deleted = on_message_deleted
        self._seen: OrderedDict[tuple[int, int], None] = OrderedDict()

    def in_scope(self, message: Message) -> bool:
        return message.chat.type in {'group', 'supergroup'} and self.scope.matches(message.chat)

    @staticmethod
    def _destination(message: Message) -> dict[str, Any]:
        params: dict[str, Any] = {'chat_id': message.chat.id, 'disable_notification': True}
        if message.message_thread_id is not None:
            params['message_thread_id'] = message.message_thread_id
        return params

    async def _send_text(self, message: Message, text: str) -> None:
        for part in _text_parts(text):
            await self.app.bot.send_message(
                **self._destination(message), text=part, parse_mode=None,
                link_preview_options=LinkPreviewOptions(is_disabled=True))

    async def _send_copy(self, message: Message, text: str) -> None:
        if message.text is None:
            photo = message.photo
            media = [('photo', photo[-1] if photo else None)] + [
                (kind, getattr(message, kind, None))
                for kind in ('animation', 'video', 'audio', 'document', 'voice')]
            for kind, source in media:
                if source is None:
                    continue
                # Long corrected captions are preserved as separate text,
                # rather than truncated to Telegram's 1024-unit limit.
                fits = len(text.encode('utf-16-le')) // 2 <= 1024
                params = {**self._destination(message), kind: source.file_id,
                          'caption': text if fits else None, 'parse_mode': None}
                if kind in {'photo', 'animation', 'video'} and message.has_media_spoiler:
                    params['has_spoiler'] = True
                await getattr(self.app.bot, 'send_' + kind)(**params)
                if not fits:
                    await self._send_text(message, text)
                return
        await self._send_text(message, text)

    async def __call__(self, handler, event: Message, data):
        if not self.in_scope(event):
            return await handler(event, data)
        user = event.from_user
        if user is None or (user.is_bot and event.sender_chat is None):
            return await handler(event, data)
        text = event.text or event.caption or ''
        if not contains_meme_words(text):
            return await handler(event, data)
        # Existing profanity moderation keeps its warning/escalation priority.
        if (event.edit_date is None
                and event.message_thread_id not in WRITERS_PROFANITY_EXEMPT_TOPIC_IDS
                and contains_prohibited_language(text)):
            return await handler(event, data)
        identity = (int(event.chat.id), int(event.message_id))
        if identity in self._seen:
            return None
        self._seen[identity] = None  # Reserve before the first await.
        if len(self._seen) > 2048:
            self._seen.popitem(last=False)
        try:
            await self.app.bot.delete_message(chat_id=event.chat.id, message_id=event.message_id)
        except Exception as exc:
            self._seen.pop(identity, None)
            LOGGER.warning('WRITERS_MEME_DELETE_FAILED reason=%s', type(exc).__name__)
            return None
        if self.on_message_deleted is not None:
            try:
                await self.on_message_deleted(int(event.chat.id), int(event.message_id))
            except Exception as exc:
                LOGGER.warning('WRITERS_MEME_PURGE_FAILED reason=%s', type(exc).__name__)
        try:
            sticker = await self.store.get(event.chat.id)
            if sticker:
                await self.app.bot.send_sticker(**self._destination(event), sticker=sticker)
        except Exception as exc:
            LOGGER.warning('WRITERS_MEME_STICKER_FAILED reason=%s', type(exc).__name__)
        rewritten = rewrite_meme_words(text)
        if is_safe_entertainment_source(rewritten):
            try:
                await self._send_copy(event, rewritten)
            except Exception as exc:
                # A timeout may have delivered the copy. Never retry it.
                LOGGER.warning('WRITERS_MEME_COPY_UNCERTAIN reason=%s', type(exc).__name__)
        LOGGER.info('WRITERS_MEME_DELETED chat_id=%s message_id=%s', *identity)
        return None


def register_writers_meme_handlers(app: Any, scope: Any, store: MemeStickerStore,
                                  on_message_deleted: Any = None) -> WritersMemeMiddleware:
    service = WritersMemeMiddleware(app, scope, store, on_message_deleted)
    app.dp.message.outer_middleware(service)
    app.dp.edited_message.outer_middleware(service)

    @app.dp.edited_message(service.in_scope,
                           lambda message: contains_meme_words(message.text or message.caption))
    async def writers_meme_edited_subscription(message: Message) -> None:
        # The outer middleware consumes matching human edits. A narrow handler
        # is still required: polling discovers update types from handlers,
        # not from middleware (resolve_used_update_types).
        return None

    async def admin(message: Message) -> bool:
        sender = message.from_user
        if sender is None or sender.is_bot or message.sender_chat is not None:
            return False
        try:
            member = await app.bot.get_chat_member(chat_id=message.chat.id, user_id=sender.id)
        except Exception as exc:
            LOGGER.warning('WRITERS_MEME_ADMIN_CHECK_FAILED reason=%s', type(exc).__name__)
            return False
        status = getattr(member.status, 'value', member.status)
        return status in {'creator', 'owner', 'administrator'}

    async def notice(message: Message, text: str) -> None:
        await app.bot.send_message(**service._destination(message), text=text, parse_mode=None)

    @app.dp.message(service.in_scope, Command('set_mog_sticker'))
    async def set_mog_sticker(message: Message) -> None:
        if not await admin(message):
            await notice(message, 'Настраивать стикер может только администратор чата.')
            return
        replied = message.reply_to_message
        sticker = replied.sticker if replied else None
        if sticker is None:
            await notice(message, 'Отправь нужный стикер и ответь на него командой /set_mog_sticker.')
            return
        try:
            await store.set(message.chat.id, sticker.file_id)
        except Exception as exc:
            LOGGER.warning('WRITERS_MEME_CONFIG_SAVE_FAILED reason=%s', type(exc).__name__)
            await notice(message, 'Не удалось подтвердить сохранение стикера. Попробуй еще раз.')
            return
        await notice(message, 'Стикер сохранен. Нежелательный мем теперь превращается в «муркнул».')

    @app.dp.message(service.in_scope, Command('mog_sticker_status'))
    async def mog_sticker_status(message: Message) -> None:
        if not await admin(message):
            await notice(message, 'Статус настройки доступен только администрации чата.')
            return
        try:
            configured = bool(await store.get(message.chat.id))
        except Exception as exc:
            LOGGER.warning('WRITERS_MEME_CONFIG_READ_FAILED reason=%s', type(exc).__name__)
            await notice(message, 'Не удалось прочитать настройку стикера. Попробуй еще раз.')
            return
        await notice(message, 'Стикер замены: ' + ('настроен.' if configured else 'не настроен.')
                     + '\nДля установки ответь на стикер командой /set_mog_sticker.')

    for name in ('set_mog_sticker', 'mog_sticker_status'):
        records = app.dp.message.handlers
        index = next(i for i, record in enumerate(records) if record.callback.__name__ == name)
        records.insert(0, records.pop(index))
    print('WRITERS_MEME_REWRITE_READY replacement=муркнуть sticker_command=/set_mog_sticker '
          'scope=writers only configured_storage=' + ('postgres' if store.postgres else 'files'), flush=True)
    return service

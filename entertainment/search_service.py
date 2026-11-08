"""Explicit factual questions, separate from local entertainment generation."""
from __future__ import annotations

import asyncio
import logging
import os
from collections import OrderedDict
from collections.abc import Callable

from aiogram.types import Message

from .moderation_safety import is_safe_entertainment_source
from .search_answers import SearchFailure, TavilySearchClient, checked_search_answer, extract_search_question
from .storage.base import EntertainmentStorage

LOGGER = logging.getLogger(__name__)
_FALLBACK = 'Пу пу пу'
_FAILURE_REASONS = frozenset({
    'missing_answer', 'answer_format', 'answer_language', 'answer_safety',
    'missing_results', 'invalid_request', 'http_error', 'response_size',
    'transport', 'response_format', 'throttled', 'unsupported_answer',
})


class SearchAnswerService:
    def __init__(self, storage: EntertainmentStorage, *,
                 is_allowed_scope: Callable[[int, int], bool],
                 quiet_hours_active: Callable,
                 now_fn: Callable[[], float]) -> None:
        self.storage = storage
        self.is_allowed_scope = is_allowed_scope
        self.quiet_hours_active = quiet_hours_active
        self.now_fn = now_fn
        key = os.getenv('TAVILY_API_KEY', '').strip()
        self.provider = TavilySearchClient(key) if key else None
        self._enabled = bool(key)
        self._seen: OrderedDict[tuple[int, int], None] = OrderedDict()
        self._last_user: OrderedDict[tuple[int, int], float] = OrderedDict()
        self._busy = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    def matches(self, message: Message) -> bool:
        if not self.enabled:
            return False
        chat_id = int(message.chat.id)
        topic_id = int(getattr(message, 'message_thread_id', None) or 0)
        if not self.is_allowed_scope(chat_id, topic_id):
            return False
        sender = getattr(message, 'from_user', None)
        if sender is None or sender.is_bot or getattr(message, 'message_id', None) is None:
            return False
        if getattr(message, 'is_automatic_forward', False) or any(
            getattr(message, field, None) is not None
            for field in ('forward_origin', 'forward_date', 'forward_from', 'forward_sender_name')
        ):
            return False
        text = getattr(message, 'text', None)
        return (extract_search_question(text) is not None
                and is_safe_entertainment_source(text))

    async def _claim(self, message: Message) -> bool:
        chat_id = int(message.chat.id)
        topic_id = int(getattr(message, 'message_thread_id', None) or 0)
        now = self.now_fn()
        settings = await self.storage.get_settings(chat_id)
        if (not settings.enabled or not settings.autonomous_text_enabled
                or self.quiet_hours_active(settings, int(now))):
            return False
        return await self.storage.claim_search_request(chat_id, topic_id, int(message.message_id), int(now))

    async def _answer(self, message: Message) -> str:
        chat_id = int(message.chat.id)
        now = self.now_fn()
        question = extract_search_question(message.text)
        if question is None or len(question) > 500:
            raise SearchFailure('invalid_request')
        user = (chat_id, int(message.from_user.id))
        previous = self._last_user.get(user)
        if self._busy or (previous is not None and now - previous < 30):
            raise SearchFailure('throttled')
        self._last_user[user] = now
        self._last_user.move_to_end(user)
        if len(self._last_user) > 1024:
            self._last_user.popitem(last=False)
        self._busy = True
        try:
            return checked_search_answer(await self.provider.answer(question))
        finally:
            self._busy = False

    async def handle(self, message: Message) -> None:
        if not self.matches(message):
            return
        identity = (int(message.chat.id), int(message.message_id))
        if identity in self._seen:
            return
        self._seen[identity] = None
        if len(self._seen) > 1024:
            self._seen.popitem(last=False)
        try:
            async with asyncio.timeout(2):
                if not await self._claim(message):
                    return
        except Exception:
            # We cannot tell whether an earlier process already answered.
            # No delivery (even a fallback) without confirmed ownership.
            LOGGER.warning('FOSGEN_SEARCH_CLAIM_UNAVAILABLE')
            return
        try:
            async with asyncio.timeout(5):
                text = await self._answer(message)
        except SearchFailure as exc:
            reason = str(exc)
            LOGGER.info('FOSGEN_SEARCH_FALLBACK reason=%s',
                        reason if reason in _FAILURE_REASONS else 'provider_failure')
            text = _FALLBACK
        except Exception:
            LOGGER.warning('FOSGEN_SEARCH_FALLBACK reason=unavailable')
            text = _FALLBACK
        if text is None:
            return
        # Delivery may already have succeeded when the connection fails. Do
        # not send a second message (including a second fallback) after it.
        try:
            await asyncio.wait_for(message.reply(
                text, parse_mode=None, disable_web_page_preview=True,
            ), timeout=5)
        except Exception:
            LOGGER.warning('FOSGEN_SEARCH_DELIVERY_UNCERTAIN')

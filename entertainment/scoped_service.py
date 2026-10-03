"""Hard topic-scope policy layered on top of Culture Memory Phase C."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from aiogram.types import CallbackQuery, Message

from .config import ENTERTAINMENT_BLOCKED_TOPIC_SCOPES
from .culture_service import EntertainmentService as PhaseCEntertainmentService
from .models import BehaviorMode, EntertainmentActionRecord
from .storage.base import EntertainmentStorage


class EntertainmentService(PhaseCEntertainmentService):
    """Phase C service with a chat+topic hard denylist.

    Denied scopes are invisible to Entertainment: no learning, active-topic
    tracking, generation, autonomous actions or control-panel mutations.
    Moderation and unrelated bot features are intentionally unaffected.
    """

    def __init__(
        self,
        app: Any,
        storage: EntertainmentStorage,
        chat_ids: Iterable[int],
        *,
        blocked_topic_scopes: Iterable[tuple[int, int]] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(app, storage, chat_ids, **kwargs)
        source = (
            ENTERTAINMENT_BLOCKED_TOPIC_SCOPES
            if blocked_topic_scopes is None
            else blocked_topic_scopes
        )
        self.blocked_topic_scopes = frozenset(
            (int(chat_id), int(topic_id)) for chat_id, topic_id in source
        )

    def is_allowed_scope(self, chat_id: int, topic_id: int) -> bool:
        return self.is_allowed_chat(int(chat_id)) and (
            int(chat_id), int(topic_id)
        ) not in self.blocked_topic_scopes

    def _message_scope_allowed(self, message: Message) -> bool:
        return self.is_allowed_scope(int(message.chat.id), self._topic_id(message))

    def remember_active_topic(self, message: Message) -> None:
        if not self._message_scope_allowed(message):
            return
        super().remember_active_topic(message)

    def is_eligible_learning_message(self, message: Message) -> bool:
        if not self._message_scope_allowed(message):
            return False
        return super().is_eligible_learning_message(message)

    def _is_eligible_memory_sender(self, message: Message) -> bool:
        if not self._message_scope_allowed(message):
            return False
        return super()._is_eligible_memory_sender(message)

    def _is_eligible_culture_sender(self, message: Message) -> bool:
        if not self._message_scope_allowed(message):
            return False
        return super()._is_eligible_culture_sender(message)

    async def evaluate_topic(
        self,
        message: Message,
        *,
        supervisor: bool = False,
    ) -> EntertainmentActionRecord | None:
        if not self._message_scope_allowed(message):
            return None
        return await super().evaluate_topic(message, supervisor=supervisor)

    async def generate_now(self, message: Message) -> None:
        if not self._message_scope_allowed(message):
            return
        await super().generate_now(message)

    async def show_panel(self, message: Message) -> None:
        if not self._message_scope_allowed(message):
            return
        await super().show_panel(message)

    async def set_enabled(self, message: Message, enabled: bool) -> None:
        if not self._message_scope_allowed(message):
            return
        await super().set_enabled(message, enabled)

    async def set_behavior_mode(self, message: Message, mode: BehaviorMode) -> None:
        if not self._message_scope_allowed(message):
            return
        await super().set_behavior_mode(message, mode)

    async def set_laziness(self, message: Message) -> None:
        if not self._message_scope_allowed(message):
            return
        await super().set_laziness(message)

    async def set_cooldown(self, message: Message) -> None:
        if not self._message_scope_allowed(message):
            return
        await super().set_cooldown(message)

    async def forget_chat(self, message: Message) -> None:
        if not self._message_scope_allowed(message):
            return
        await super().forget_chat(message)

    async def set_remember_me(self, message: Message, enabled: bool) -> None:
        if not self._message_scope_allowed(message):
            return
        await super().set_remember_me(message, enabled)

    async def delete_my_memory(self, message: Message) -> int:
        if not self._message_scope_allowed(message):
            return 0
        return await super().delete_my_memory(message)

    async def handle_callback(self, callback: CallbackQuery) -> None:
        message = callback.message
        if message is not None and not self._message_scope_allowed(message):
            await callback.answer()
            return
        await super().handle_callback(callback)

    async def run_supervisor_tick(self) -> None:
        # Remove any stale scopes remembered before a config/code rollout, then
        # delegate to the normal bounded supervisor.
        for key in tuple(self._active_topics):
            chat_id, topic_id = key
            if not self.is_allowed_scope(chat_id, topic_id):
                self._active_topics.pop(key, None)
        await super().run_supervisor_tick()


__all__ = ["EntertainmentService"]

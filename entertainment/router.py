"""Aiogram filters, middleware and handler registration for entertainment."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from aiogram import BaseMiddleware, F
from aiogram.filters import BaseFilter, Command
from aiogram.types import CallbackQuery, Message

from .service import EntertainmentService

LOGGER = logging.getLogger(__name__)


class EntertainmentChatFilter(BaseFilter):
    """Match only explicitly allowlisted entertainment chats."""

    __slots__ = ("chat_ids",)

    def __init__(self, chat_ids: Iterable[int]) -> None:
        self.chat_ids = frozenset(int(chat_id) for chat_id in chat_ids)

    async def __call__(self, message: Message) -> bool:
        return int(message.chat.id) in self.chat_ids


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

    @dispatcher.message(chat_filter, Command(commands=["fun_ignore_me"]))
    async def entertainment_ignore_me(message: Message) -> None:
        await service.set_remember_me(message, False)

    _promote_last_message_handler(app)

    @dispatcher.message(chat_filter, Command(commands=["fun_remember_me"]))
    async def entertainment_remember_me(message: Message) -> None:
        await service.set_remember_me(message, True)

    _promote_last_message_handler(app)

    @dispatcher.message(chat_filter, Command(commands=["fun_delete_me"]))
    async def entertainment_delete_me(message: Message) -> None:
        await service.delete_my_memory(message)

    _promote_last_message_handler(app)

    # One-release compatibility aliases. They no longer mutate numeric behavior;
    # the service only points administrators to the mode-based /fun panel.
    @dispatcher.message(chat_filter, Command(commands=["fun_laziness"]))
    async def entertainment_laziness_compat(message: Message) -> None:
        await service.set_laziness(message)

    _promote_last_message_handler(app)

    @dispatcher.message(chat_filter, Command(commands=["fun_cooldown"]))
    async def entertainment_cooldown_compat(message: Message) -> None:
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
        f"chat_ids={ids_label} learning=per_topic autonomy=state_driven "
        "behavior_modes=calm,alive,active culture_memory=on external_ai=off",
        flush=True,
    )

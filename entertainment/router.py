"""Aiogram filters, middleware and handler registration for entertainment."""

from __future__ import annotations

import asyncio
import logging
import time
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
    """Keep PostgreSQL culture-memory outages off the Telegram polling path.

    Work is serialized to preserve per-chat observation order, and bounded
    so a prolonged database outage cannot consume unbounded process memory.
    Commands and moderation always continue, even if learning is degraded.
    """

    def __init__(
        self,
        service: EntertainmentService,
        *,
        max_pending: int = 128,
        observe_timeout: float = 8.0,
    ) -> None:
        self.service = service
        self._queue: asyncio.Queue[Message] = asyncio.Queue(maxsize=max(1, max_pending))
        self._worker: asyncio.Task[None] | None = None
        self._last_warning_at = -60.0
        self._observe_timeout = max(1.0, float(observe_timeout))

    async def _drain(self) -> None:
        while True:
            event = await self._queue.get()
            try:
                await asyncio.wait_for(
                    self.service.observe_message(event),
                    timeout=self._observe_timeout,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                now = time.monotonic()
                if now - self._last_warning_at >= 60.0:
                    self._last_warning_at = now
                    LOGGER.warning(
                        "ENTERTAINMENT_LEARNING_DEGRADED error_type=%s "
                        "queued=%s",
                        type(exc).__name__, self._queue.qsize(),
                    )
                # Slow repeated connection attempts should not monopolize
                # the provider's limited Postgres connection capacity.
                await asyncio.sleep(3)
            finally:
                self._queue.task_done()

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        chat_id = int(getattr(getattr(event, "chat", None), "id", 0) or 0)
        if chat_id not in self.service.chat_ids:
            return await handler(event, data)
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(
                self._drain(), name="entertainment-learning",
            )
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            now = time.monotonic()
            if now - self._last_warning_at >= 60.0:
                self._last_warning_at = now
                LOGGER.warning(
                    "ENTERTAINMENT_LEARNING_QUEUE_FULL "
                    "max_pending=%s; learning temporarily skipped",
                    self._queue.maxsize,
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

    @dispatcher.message(chat_filter, Command(commands=["fun_generation_status", "fun_gen_status"]))
    async def entertainment_generation_status(message: Message) -> None:
        await service.show_generation_status(message)

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

    @dispatcher.message(chat_filter, Command(commands=["fun_style"]))
    async def entertainment_personality_style(message: Message) -> None:
        await service.configure_personality_style(message)

    _promote_last_message_handler(app)

    @dispatcher.message(chat_filter, Command(commands=["fun_topic"]))
    async def entertainment_personality_topic(message: Message) -> None:
        await service.configure_personality_topic(message)

    _promote_last_message_handler(app)

    @dispatcher.message(chat_filter, Command(commands=["fun_profile"]))
    async def entertainment_personality_profile(message: Message) -> None:
        await service.show_personality_profile(message)

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

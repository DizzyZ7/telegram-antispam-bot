from __future__ import annotations

import asyncio
import html
import logging
from contextlib import suppress
from typing import Any

from .models import OutboxEventType, ReviewAction

LOGGER = logging.getLogger(__name__)


def _escape(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def _action_value(value: object) -> str:
    if isinstance(value, ReviewAction):
        return value.value
    return str(value)


def _author_notification_text(context: Any) -> str:
    action = _action_value(context.action)
    title = _escape(context.title)
    comment = getattr(context, "comment", None)

    if action == ReviewAction.APPROVE.value:
        lines = [
            "✅ <b>Работа одобрена</b>",
            "",
            f"<b>{title}</b>",
        ]
    elif action == ReviewAction.REQUEST_CHANGES.value:
        lines = [
            "✏️ <b>Нужны правки</b>",
            "",
            f"<b>{title}</b>",
        ]
    elif action == ReviewAction.REJECT.value:
        lines = [
            "❌ <b>Работа отклонена</b>",
            "",
            f"<b>{title}</b>",
        ]
    else:
        lines = [
            "ℹ️ <b>Статус работы изменен</b>",
            "",
            f"<b>{title}</b>",
        ]

    if comment:
        lines.extend(("", f"Комментарий: {_escape(comment)}"))
    return "\n".join(lines)


def _moderation_text(context: Any) -> str:
    lines = [
        "✒️ <b>Новая работа на модерацию</b>",
        "",
        f"Автор: <code>{int(context.author_user_id)}</code>",
        f"Название: {_escape(context.title)}",
        f"Тип: {_escape(context.work_type)}",
        f"Жанр: {_escape(context.genre)}",
        "",
        f"Описание: {_escape(context.description)}",
    ]
    if getattr(context, "external_url", None):
        lines.extend(("", f"Ссылка: {_escape(context.external_url)}"))
    body = str(getattr(context, "body_text", "") or "").strip()
    if body:
        preview = body[:1500]
        if len(body) > len(preview):
            preview += "…"
        lines.extend(("", "<b>Текст:</b>", _escape(preview)))
    return "\n".join(lines)


def _is_permanent_telegram_error(exc: Exception) -> bool:
    name = type(exc).__name__.casefold()
    return (
        "forbidden" in name
        or "unauthorized" in name
        or "badrequest" in name
    )


class WritersDeliveryWorker:
    def __init__(
        self,
        bot: Any,
        storage: Any,
        config: Any,
        *,
        poll_seconds: int = 2,
        lease_seconds: int = 60,
        batch_size: int = 10,
        worker_id: str = "writers-delivery",
    ) -> None:
        self.bot = bot
        self.storage = storage
        self.config = config
        self.poll_seconds = max(1, int(poll_seconds))
        self.lease_seconds = max(1, int(lease_seconds))
        self.batch_size = max(1, int(batch_size))
        self.worker_id = str(worker_id)
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(
            self._run_forever(),
            name=f"writers-delivery:{self.worker_id}",
        )

    async def stop(self) -> None:
        self._stop_event.set()
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _run_forever(self) -> None:
        while not self._stop_event.is_set():
            try:
                now = int(asyncio.get_running_loop().time())
                await self.run_once(now=now)
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("WRITERS_DELIVERY_LOOP_FAILED")
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.poll_seconds,
                )
            except TimeoutError:
                pass

    async def run_once(self, *, now: int) -> int:
        items = await self.storage.claim_due_outbox(
            worker_id=self.worker_id,
            now=int(now),
            lease_seconds=self.lease_seconds,
            limit=self.batch_size,
        )
        for item in items:
            try:
                await self._deliver(item)
            except Exception as exc:
                if _is_permanent_telegram_error(exc):
                    await self.storage.mark_outbox_permanent_failure(
                        outbox_id=item.id,
                        worker_id=self.worker_id,
                        now=int(now),
                        error_code=type(exc).__name__,
                    )
                else:
                    base_delay = min(
                        300,
                        2 ** min(max(0, int(item.attempt_count)), 8),
                    )
                    jitter = int(item.id.int % 7)
                    await self.storage.mark_outbox_retryable(
                        outbox_id=item.id,
                        worker_id=self.worker_id,
                        now=int(now),
                        next_attempt_at=int(now) + base_delay + jitter,
                        error_code=type(exc).__name__,
                    )
                continue

            await self.storage.mark_outbox_delivered(
                outbox_id=item.id,
                worker_id=self.worker_id,
                now=int(now),
            )
        return len(items)

    async def _deliver(self, item: Any) -> None:
        event_type = OutboxEventType(item.event_type)
        if event_type is OutboxEventType.MODERATION_CARD:
            await self._deliver_moderation_card(item)
            return
        if event_type is OutboxEventType.AUTHOR_NOTIFICATION:
            await self._deliver_author_notification(item)
            return
        raise RuntimeError("unsupported outbox event type")

    async def _deliver_moderation_card(self, item: Any) -> None:
        context = await self.storage.get_moderation_delivery_context(
            submission_id=item.submission_id,
            revision_id=item.revision_id,
        )
        moderation_chat_id = int(self.config.moderation_chat_id)
        for attachment in context.files:
            await self.bot.send_document(
                chat_id=moderation_chat_id,
                document=attachment.telegram_file_id,
                caption=_escape(attachment.safe_filename),
                parse_mode="HTML",
            )
        await self.bot.send_message(
            chat_id=moderation_chat_id,
            text=_moderation_text(context),
            parse_mode="HTML",
        )

    async def _deliver_author_notification(self, item: Any) -> None:
        context = await self.storage.get_author_notification_context(
            submission_id=item.submission_id,
            revision_id=item.revision_id,
        )
        await self.bot.send_message(
            chat_id=int(context.author_user_id),
            text=_author_notification_text(context),
            parse_mode="HTML",
        )

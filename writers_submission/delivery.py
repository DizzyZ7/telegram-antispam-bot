from __future__ import annotations

import asyncio
import html
import logging
import time
from contextlib import suppress
from io import BytesIO

from aiogram.types import BufferedInputFile
from typing import Any

from .handlers import build_moderation_keyboard
from .models import OutboxEventType, ReviewAction
from .promo import render_owner_promo, render_palette_png

LOGGER = logging.getLogger(__name__)


def _escape(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


# Telegram limits text messages to 4096 characters after entity parsing.
# Keep raw HTML, including escaped entities and astral Unicode, below this
# conservative UTF-16-unit threshold to avoid permanently failed outbox jobs.
_TELEGRAM_SAFE_MESSAGE_UNITS = 3900


def _utf16_units(text: str) -> int:
    return sum(2 if ord(character) > 0xFFFF else 1 for character in text)


def _escaped_preview(value: object, *, max_units: int) -> str:
    original = str(value or "").strip()
    escaped = html.escape(original, quote=True)
    if _utf16_units(escaped) <= max_units:
        return escaped

    # Limit before escaping complete characters: never split an HTML entity
    # such as &amp; or an astral Unicode character.
    parts: list[str] = []
    used = 0
    for character in original:
        fragment = html.escape(character, quote=True)
        units = _utf16_units(fragment)
        if used + units + 1 > max_units:
            break
        parts.append(fragment)
        used += units
    return "".join(parts) + "…"


def _action_value(value: object) -> str:
    if isinstance(value, ReviewAction):
        return value.value
    return str(value)


def _author_notification_text(context: Any) -> str:
    action = _action_value(context.action)
    title = _escaped_preview(context.title, max_units=500)
    comment = getattr(context, "comment", None)

    if action in {"SUBMISSION_ACCEPTED"}:
        lines = [
            "📨 <b>Работа принята и отправлена на проверку</b>",
            "",
            f"<b>{title}</b>",
        ]
    elif action in {ReviewAction.APPROVE.value, "APPROVED"}:
        lines = [
            "✅ <b>Работа одобрена</b>",
            "",
            f"<b>{title}</b>",
        ]
    elif action in {ReviewAction.REQUEST_CHANGES.value, "CHANGES_REQUESTED"}:
        lines = [
            "✏️ <b>Нужны правки</b>",
            "",
            f"<b>{title}</b>",
        ]
    elif action in {ReviewAction.REJECT.value, "REJECTED"}:
        lines = [
            "❌ <b>Работа отклонена</b>",
            "",
            f"<b>{title}</b>",
        ]
    elif action == "WITHDRAWN":
        lines = [
            "↩️ <b>Работа отозвана</b>",
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
        lines.extend(
            ("", f"Комментарий: {_escaped_preview(comment, max_units=3000)}")
        )
    return "\n".join(lines)


def _moderation_text(context: Any) -> str:
    details = getattr(context, "details", None) or {}
    is_ficbook_form = details.get("form_version") == 2
    lines = [
        "✒️ <b>Новая работа на модерацию</b>",
        "",
        f"Автор: <code>{int(context.author_user_id)}</code>",
        f"Название: {_escaped_preview(context.title, max_units=300)}",
        f"Тип: {_escaped_preview(context.work_type, max_units=80)}",
        f"{'Направление' if is_ficbook_form else 'Жанр'}: {_escaped_preview(context.genre, max_units=130)}",
    ]
    if is_ficbook_form:
        for label, key in (
            ("Фандом", "fandom"),
            ("Размер", "size_category"),
            ("Рейтинг", "rating"),
            ("Статус", "completion"),
            ("Слов", "size_words"),
            ("Страниц", "pages"),
            ("Частей", "parts"),
        ):
            value = details.get(key)
            if value not in (None, ""):
                lines.append(f"{label}: {_escaped_preview(value, max_units=130)}")
        if details.get("visual_mode") == "palette":
            colors = details.get("palette_colors") or []
            lines.append(f"Палитра RGB: {_escaped_preview(', '.join(colors), max_units=120)}")
        elif details.get("visual_mode") == "image":
            lines.append("Оформление: картинка во вложениях")
        links = details.get("extra_links") or []
        if links:
            lines.append(f"Доп. ссылки: {_escaped_preview(' | '.join(links), max_units=450)}")
    lines.extend(("", f"Описание: {_escaped_preview(context.description, max_units=620)}"))
    if getattr(context, "external_url", None):
        label = "Фикбук" if is_ficbook_form else "Ссылка"
        lines.extend(
            ("", f"{label}: {_escaped_preview(context.external_url, max_units=700)}")
        )
    body = str(getattr(context, "body_text", "") or "").strip()
    if body:
        preview = body[:1200]
        if len(body) > len(preview):
            preview += "…"
        lines.extend(("", "<b>Текст:</b>", _escaped_preview(preview, max_units=620)))
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
        now_fn: Any = time.time,
    ) -> None:
        self.bot = bot
        self.storage = storage
        self.config = config
        self.poll_seconds = max(1, int(poll_seconds))
        self.lease_seconds = max(1, int(lease_seconds))
        self.batch_size = max(1, int(batch_size))
        self.worker_id = str(worker_id)
        self.now_fn = now_fn
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
                now = int(self.now_fn())
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
                delivery_chat_id, delivery_message_ids = await self._deliver(
                    item,
                    now=int(now),
                )
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
                delivery_chat_id=delivery_chat_id,
                delivery_message_ids=delivery_message_ids,
            )
        return len(items)

    async def _deliver(
        self,
        item: Any,
        *,
        now: int,
    ) -> tuple[int, tuple[int, ...]]:
        event_type = OutboxEventType(item.event_type)
        if event_type is OutboxEventType.MODERATION_CARD:
            return await self._deliver_moderation_card(item, now=now)
        if event_type is OutboxEventType.AUTHOR_NOTIFICATION:
            return await self._deliver_author_notification(item)
        if event_type is OutboxEventType.OWNER_PREVIEW:
            return await self._deliver_owner_preview(item)
        raise RuntimeError("unsupported outbox event type")

    async def _deliver_moderation_card(
        self,
        item: Any,
        *,
        now: int,
    ) -> tuple[int, tuple[int, ...]]:
        token = await self.storage.get_or_create_moderation_token(
            submission_id=item.submission_id,
            revision_id=item.revision_id,
            now=int(now),
        )
        context = await self.storage.get_moderation_delivery_context(
            submission_id=item.submission_id,
            revision_id=item.revision_id,
        )
        moderation_chat_id = int(self.config.moderation_chat_id)
        message_ids: list[int] = []
        for attachment in context.files:
            sent = await self.bot.send_document(
                chat_id=moderation_chat_id,
                document=attachment.telegram_file_id,
                caption=_escape(attachment.safe_filename),
                parse_mode="HTML",
            )
            message_id = getattr(sent, "message_id", None)
            if message_id is not None:
                message_ids.append(int(message_id))
        card = await self.bot.send_message(
            chat_id=moderation_chat_id,
            text=_moderation_text(context),
            parse_mode="HTML",
            reply_markup=build_moderation_keyboard(token),
        )
        card_message_id = getattr(card, "message_id", None)
        if card_message_id is not None:
            message_ids.append(int(card_message_id))
        return moderation_chat_id, tuple(message_ids)

    async def _deliver_author_notification(
        self,
        item: Any,
    ) -> tuple[int, tuple[int, ...]]:
        payload = getattr(item, "payload", None)
        if not isinstance(payload, dict):
            payload = {}
        notification_kind = payload.get("kind") or payload.get("action")
        context = await self.storage.get_author_notification_context(
            submission_id=item.submission_id,
            revision_id=item.revision_id,
            notification_kind=(
                str(notification_kind)
                if notification_kind is not None
                else None
            ),
        )
        author_user_id = int(context.author_user_id)
        sent = await self.bot.send_message(
            chat_id=author_user_id,
            text=_author_notification_text(context),
            parse_mode="HTML",
        )
        message_id = getattr(sent, "message_id", None)
        return (
            author_user_id,
            (int(message_id),) if message_id is not None else (),
        )

    async def _deliver_owner_preview(
        self,
        item: Any,
    ) -> tuple[int, tuple[int, ...]]:
        """A review-approved publication draft is sent to owner DM only.

        Do not use WRITERS_CHAT_ID / moderation_chat_id as the destination,
        and do not auto-publish into a channel.
        """
        owner_id = int(getattr(self.config, "owner_user_id", 2039781854))
        if owner_id <= 0:
            raise ValueError("Writers owner Telegram ID must be positive")
        context = await self.storage.get_moderation_delivery_context(
            submission_id=item.submission_id,
            revision_id=item.revision_id,
        )
        post = render_owner_promo(context)
        details = context.details or {}
        visual_mode = details.get("visual_mode")
        photo = None
        if visual_mode == "palette":
            photo = BufferedInputFile(
                render_palette_png(details.get("palette_colors", [])),
                filename="writers-palette.png",
            )
        elif visual_mode == "image":
            image = next(
                (f for f in context.files if f.detected_file_class in {"png", "jpeg"}),
                None,
            )
            if image is None:
                raise ValueError("Approved work has no illustration attachment")
            image_bytes = BytesIO()
            await self.bot.download(image.telegram_file_id, destination=image_bytes)
            data = image_bytes.getvalue()
            if not data:
                raise RuntimeError("Telegram returned empty approved illustration")
            photo = BufferedInputFile(data, filename=image.safe_filename)

        message_ids: list[int] = []
        if photo is not None:
            # Photo caption has a separate 1024-character Telegram limit.
            # Long ICФ posts are sent as a photo plus a complete copyable text.
            caption = post if _utf16_units(post) <= 950 else "🖼️ Обложка одобренной работы. Готовый пост — следующим сообщением."
            if isinstance(photo, BufferedInputFile) and len(photo.data) > 10 * 1024 * 1024:
                photo_message = await self.bot.send_document(
                    chat_id=owner_id,
                    document=photo,
                    caption="Иллюстрация к одобренной работе (размер более 10 МБ).",
                )
                caption = None
            else:
                photo_message = await self.bot.send_photo(
                    chat_id=owner_id,
                    photo=photo,
                    caption=caption,
                    parse_mode="HTML",
                )
            message_id = getattr(photo_message, "message_id", None)
            if message_id is not None:
                message_ids.append(int(message_id))
            if caption == post:
                return owner_id, tuple(message_ids)

        published_text = await self.bot.send_message(
            chat_id=owner_id,
            text=post,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        message_id = getattr(published_text, "message_id", None)
        if message_id is not None:
            message_ids.append(int(message_id))
        return owner_id, tuple(message_ids)

from __future__ import annotations

import html
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Callable
from uuid import UUID

from aiogram import F
from aiogram.filters import BaseFilter, Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

from .models import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ReviewAction,
    ValidationError,
)

LOGGER = logging.getLogger(__name__)

_CALLBACK_PREFIX = "ws"
_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,48}$")
_COMMENT_TTL_SECONDS = 10 * 60
_MAX_PENDING_COMMENTS = 256


@dataclass(frozen=True, slots=True)
class PendingModerationComment:
    token: str
    submission_id: UUID
    revision_id: UUID
    reviewer_user_id: int
    prompt_message_id: int
    created_at: float
    expires_at: float


class PendingCommentStore:
    def __init__(
        self,
        *,
        ttl_seconds: int = _COMMENT_TTL_SECONDS,
        max_entries: int = _MAX_PENDING_COMMENTS,
    ) -> None:
        self.ttl_seconds = max(1, int(ttl_seconds))
        self.max_entries = max(1, int(max_entries))
        self._items: dict[tuple[int, str], PendingModerationComment] = {}

    def put(
        self,
        *,
        reviewer_user_id: int,
        token: str,
        submission_id: UUID,
        revision_id: UUID,
        prompt_message_id: int,
        now: float,
    ) -> PendingModerationComment:
        reviewer_user_id = int(reviewer_user_id)
        # A moderator can have only one active free-form decision at a time.
        for key in tuple(self._items):
            if key[0] == reviewer_user_id:
                self._items.pop(key, None)

        item = PendingModerationComment(
            token=token,
            submission_id=submission_id,
            revision_id=revision_id,
            reviewer_user_id=reviewer_user_id,
            prompt_message_id=int(prompt_message_id),
            created_at=float(now),
            expires_at=float(now) + self.ttl_seconds,
        )
        self._items[(reviewer_user_id, token)] = item

        if len(self._items) > self.max_entries:
            oldest_key = min(
                self._items,
                key=lambda key: self._items[key].created_at,
            )
            self._items.pop(oldest_key, None)
        return item

    def current_for_user(
        self,
        reviewer_user_id: int,
    ) -> PendingModerationComment | None:
        reviewer_user_id = int(reviewer_user_id)
        candidates = [
            item
            for (user_id, _), item in self._items.items()
            if user_id == reviewer_user_id
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda item: item.created_at)

    def pop(self, reviewer_user_id: int, token: str) -> None:
        self._items.pop((int(reviewer_user_id), token), None)

    def has_for_user(self, reviewer_user_id: int) -> bool:
        reviewer_user_id = int(reviewer_user_id)
        return any(key[0] == reviewer_user_id for key in self._items)


class WritersSubmissionStartFilter(BaseFilter):
    """Match only the private /start writers_submit deep-link entry."""

    async def __call__(self, message: Message) -> bool:
        return (
            _chat_type_value(message.chat) == "private"
            and _is_writers_submit_start(message)
        )


class PendingModerationCommentFilter(BaseFilter):
    def __init__(
        self,
        store: PendingCommentStore,
        moderation_chat_id: int,
    ) -> None:
        self.store = store
        self.moderation_chat_id = int(moderation_chat_id)

    async def __call__(self, message: Message) -> bool:
        if getattr(message, "from_user", None) is None:
            return False
        if int(getattr(message.chat, "id", 0)) != self.moderation_chat_id:
            return False
        item = self.store.current_for_user(int(message.from_user.id))
        return item is not None and _is_prompt_reply(message, item)


def _is_prompt_reply(message: Message, item: PendingModerationComment) -> bool:
    reply = getattr(message, "reply_to_message", None)
    return (
        reply is not None
        and getattr(reply, "message_id", None) == item.prompt_message_id
    )


def build_moderation_keyboard(token: str, *, paid_cover: bool = False) -> InlineKeyboardMarkup:
    if not _TOKEN_PATTERN.fullmatch(token):
        raise ValueError("Invalid moderation token")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Взять на проверку",
                    callback_data=f"{_CALLBACK_PREFIX}:c:{token}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Одобрить",
                    callback_data=f"{_CALLBACK_PREFIX}:a:{token}",
                ),
                InlineKeyboardButton(
                    text="Нужны правки",
                    callback_data=f"{_CALLBACK_PREFIX}:x:{token}",
                ),
                InlineKeyboardButton(
                    text="Отклонить",
                    callback_data=f"{_CALLBACK_PREFIX}:r:{token}",
                ),
            ],
            *([[
                InlineKeyboardButton(
                    text="💎 Подтвердить оплату своей обложки",
                    callback_data=f"{_CALLBACK_PREFIX}:p:{token}",
                ),
            ]] if paid_cover else []),
        ]
    )


def _parse_callback(data: str | None) -> tuple[str, str] | None:
    if not isinstance(data, str):
        return None
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != _CALLBACK_PREFIX:
        return None
    action, token = parts[1], parts[2]
    if action not in {"c", "a", "x", "r", "p"}:
        return None
    if not _TOKEN_PATTERN.fullmatch(token):
        return None
    return action, token


def _chat_type_value(chat: Any) -> str:
    value = getattr(chat, "type", "")
    return str(getattr(value, "value", value))


def _is_writers_submit_start(message: Message) -> bool:
    text = str(getattr(message, "text", "") or "").strip()
    if not text:
        return False
    parts = text.split(maxsplit=1)
    return len(parts) == 2 and parts[1].strip() == "writers_submit"


def _promote_registered_handler(observer: Any, callback: Any) -> None:
    """Move exactly one newly registered handler ahead of legacy catchalls."""
    handlers = getattr(observer, "handlers", None)
    if not isinstance(handlers, list):
        return
    for index in range(len(handlers) - 1, -1, -1):
        item = handlers[index]
        if getattr(item, "callback", None) is callback:
            handlers.insert(0, handlers.pop(index))
            return


def register_writers_submission_handlers(
    app: Any,
    service: Any,
    config: Any,
    *,
    now_fn: Callable[[], float] = time.time,
) -> PendingCommentStore:
    pending_comments = PendingCommentStore()
    message_handler_start = len(app.dp.message.handlers)
    callback_handler_start = len(app.dp.callback_query.handlers)
    moderation_chat_id = int(config.moderation_chat_id)
    moderator_ids = frozenset(int(value) for value in config.moderator_ids)

    @app.dp.message(CommandStart(), WritersSubmissionStartFilter())
    async def writers_submission_start(message: Message) -> None:
        if not _is_writers_submit_start(message):
            return
        if _chat_type_value(message.chat) != "private":
            return
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✒️ Отправить работу",
                        web_app=WebAppInfo(url=str(config.public_url)),
                    )
                ]
            ]
        )
        await message.answer(
            "✒️ Открой Mini App, чтобы создать черновик или отправить работу.",
            reply_markup=keyboard,
        )

    @app.dp.message(Command("writers_delivery"))
    async def writers_delivery_status(message: Message) -> None:
        """Operational diagnostics only in the owner's Telegram private DM."""
        if (
            _chat_type_value(message.chat) != "private"
            or getattr(message, "from_user", None) is None
            or int(message.from_user.id) != int(config.owner_user_id)
        ):
            return
        try:
            items = await service.storage.get_owner_preview_delivery_status()
        except Exception:
            LOGGER.exception("WRITERS_OWNER_DELIVERY_STATUS_FAILED")
            await message.answer("Не удалось прочитать очередь доставки. Проверь логи.")
            return
        if not items:
            await message.answer(
                "📬 Очередь готовых постов пока пуста. "
                "Если заявка одобрена в старой версии, используй "
                "/writers_retry ID_заявки."
            )
            return
        lines = ["📬 Доставка готовых постов ИКФ"]
        for item in items:
            state = str(item["state"])
            mark = {
                "DELIVERED": "✅", "PENDING": "🕐",
                "IN_FLIGHT": "🚚", "RETRYABLE_FAILED": "🔄",
                "PERMANENT_FAILED": "❌",
            }.get(state, "❔")
            title = html.escape(str(item["title"])[:70])
            detail = (
                f" · ошибка {html.escape(str(item['last_error_code']))}"
                if item.get("last_error_code") else ""
            )
            lines.append(
                f"{mark} <b>{title}</b> · {html.escape(state)}{detail}"
                f"\n<code>{item['submission_id']}</code>"
            )
        lines.append(
            "\nЕсли ошибка исправлена, отправь "
            "<code>/writers_retry ID_заявки</code>. "
            "Повторно доставленные посты проверяй перед публикацией."
        )
        await message.answer("\n\n".join(lines), parse_mode="HTML")

    @app.dp.message(Command("writers_retry"))
    async def writers_retry_preview(message: Message) -> None:
        if (
            _chat_type_value(message.chat) != "private"
            or getattr(message, "from_user", None) is None
            or int(message.from_user.id) != int(config.owner_user_id)
        ):
            return
        parts = str(message.text or "").split(maxsplit=1)
        submission_id = None
        if len(parts) == 2:
            try:
                submission_id = UUID(parts[1].strip())
            except (ValueError, TypeError):
                await message.answer("Формат: /writers_retry UUID_заявки")
                return
        try:
            target = await service.storage.retry_failed_owner_preview(
                submission_id=submission_id,
                now=int(now_fn()),
            )
        except Exception:
            LOGGER.exception("WRITERS_OWNER_DELIVERY_RETRY_FAILED")
            await message.answer("Не удалось восстановить доставку. Проверь логи.")
            return
        if target is None:
            await message.answer(
                "Нет подходящей одобренной заявки с ошибкой доставки. "
                "Уже доставленные посты автоматически не дублируются. "
                "Проверь /writers_delivery."
            )
            return
        LOGGER.warning(
            "WRITERS_OWNER_PREVIEW_MANUAL_REQUEUED submission_id=%s owner=%s",
            target, int(config.owner_user_id),
        )
        await message.answer(
            f"🔁 Пост <code>{target}</code> поставлен в очередь повторно. "
            "Обычно доставка занимает несколько секунд. "
            "Статус: /writers_delivery",
            parse_mode="HTML",
        )

    @app.dp.callback_query(F.data.startswith(f"{_CALLBACK_PREFIX}:"))
    async def writers_moderation_callback(callback: CallbackQuery) -> None:
        actor_id = int(callback.from_user.id)
        if actor_id not in moderator_ids:
            await callback.answer(
                "Недостаточно прав для модерации.",
                show_alert=True,
            )
            return
        if (
            callback.message is None
            or int(getattr(callback.message.chat, "id", 0)) != moderation_chat_id
        ):
            await callback.answer(
                "Эта кнопка доступна только в чате модерации.",
                show_alert=True,
            )
            return

        parsed = _parse_callback(callback.data)
        if parsed is None:
            await callback.answer("Кнопка устарела или повреждена.", show_alert=True)
            return
        action_code, token = parsed

        try:
            target = await service.resolve_moderation_token(token)
        except (NotFoundError, ConflictError):
            await callback.answer("Эта карточка устарела.", show_alert=True)
            return
        except Exception:
            LOGGER.exception("WRITERS_MODERATION_TOKEN_RESOLVE_FAILED")
            await callback.answer("Модерация временно недоступна.", show_alert=True)
            return

        now = int(now_fn())

        if action_code == "x":
            try:
                prompt = await app.bot.send_message(
                    chat_id=moderation_chat_id,
                    text=(
                        "✏️ Ответь реплаем на это сообщение с комментарием "
                        "к правкам в течение 10 минут. Другие сообщения "
                        "в чате не будут засчитаны."
                    ),
                    reply_to_message_id=getattr(
                        callback.message, "message_id", None
                    ),
                )
                prompt_message_id = int(getattr(prompt, "message_id", 0))
                if prompt_message_id <= 0:
                    raise RuntimeError("Telegram prompt has no message_id")
            except Exception:
                # A failed Telegram prompt must never arm a free-form
                # message capture in the moderation chat.
                LOGGER.exception("WRITERS_MODERATION_COMMENT_PROMPT_FAILED")
                await callback.answer(
                    "Не удалось запросить комментарий. Попробуй еще раз.",
                    show_alert=True,
                )
                return
            pending_comments.put(
                reviewer_user_id=actor_id,
                token=token,
                submission_id=target.submission_id,
                revision_id=target.revision_id,
                prompt_message_id=prompt_message_id,
                now=float(now_fn()),
            )
            await callback.answer("Жду ответ на сообщение")
            return

        try:
            if action_code == "p":
                applied = await service.confirm_cover_payment(
                    reviewer_user_id=actor_id,
                    submission_id=target.submission_id,
                    revision_id=target.revision_id,
                    now=now,
                )
                answer_text = (
                    "Оплата своей обложки подтверждена"
                    if applied else "Оплата уже подтверждена"
                )
            elif action_code == "c":
                result = await service.claim(
                    reviewer_user_id=actor_id,
                    submission_id=target.submission_id,
                    revision_id=target.revision_id,
                    now=now,
                )
                answer_text = (
                    "Работа взята на проверку"
                    if result.applied
                    else "Работа уже у тебя на проверке"
                )
            else:
                decision = {
                    "a": ReviewAction.APPROVE,
                    "r": ReviewAction.REJECT,
                }[action_code]
                result = await service.decide(
                    reviewer_user_id=actor_id,
                    submission_id=target.submission_id,
                    revision_id=target.revision_id,
                    action=decision,
                    comment=None,
                    now=now,
                )
                answer_text = (
                    "Решение сохранено"
                    if result.applied
                    else "Решение уже применено"
                )
        except AuthorizationError:
            await callback.answer("Недостаточно прав для модерации.", show_alert=True)
            return
        except ValidationError as exc:
            await callback.answer(
                "Для платной обложки сначала подтверди оплату у владельца ИКФ."
                if "Paid custom cover" in str(exc) else str(exc)[:180],
                show_alert=True,
            )
            return
        except (ConflictError, NotFoundError):
            await callback.answer("Состояние работы уже изменилось.", show_alert=True)
            return
        except Exception:
            LOGGER.exception(
                "WRITERS_MODERATION_CALLBACK_FAILED actor_id=%s",
                actor_id,
            )
            await callback.answer("Модерация временно недоступна.", show_alert=True)
            return

        try:
            if action_code in {"a", "r"}:
                await callback.message.edit_reply_markup(reply_markup=None)
            else:
                await callback.message.edit_reply_markup(
                    reply_markup=build_moderation_keyboard(
                        token,
                        paid_cover=(
                            getattr(
                                getattr(result, "submission", None),
                                "revision", None,
                            ) is not None
                            and getattr(result.submission.revision, "details", {}).get("visual_mode") == "image"
                        ) if action_code == "c" else action_code == "p",
                    )
                )
        except Exception as exc:
            # Telegram returns this when the claim/paid-cover keyboard is
            # already identical; it does not affect the saved review decision.
            if "message is not modified" in str(exc).casefold():
                LOGGER.debug("WRITERS_MODERATION_CARD_UNCHANGED")
            else:
                LOGGER.warning("WRITERS_MODERATION_CARD_EDIT_FAILED", exc_info=True)

        await callback.answer(answer_text)

    @app.dp.message(
        PendingModerationCommentFilter(
            pending_comments,
            moderation_chat_id,
        ),
        F.text,
    )
    async def writers_moderation_comment(message: Message) -> None:
        actor_id = int(message.from_user.id)
        item = pending_comments.current_for_user(actor_id)
        if item is None or not _is_prompt_reply(message, item):
            return

        now_value = float(now_fn())
        if now_value > item.expires_at:
            pending_comments.pop(actor_id, item.token)
            await message.answer(
                "Срок ожидания комментария истек. Нажми «Нужны правки» еще раз."
            )
            return

        comment = str(message.text or "").strip()
        if not comment:
            await message.answer("Комментарий не может быть пустым.")
            return

        try:
            result = await service.decide(
                reviewer_user_id=actor_id,
                submission_id=item.submission_id,
                revision_id=item.revision_id,
                action=ReviewAction.REQUEST_CHANGES,
                comment=comment,
                now=int(now_value),
            )
        except AuthorizationError:
            pending_comments.pop(actor_id, item.token)
            await message.answer("Недостаточно прав для модерации.")
            return
        except (ConflictError, NotFoundError):
            pending_comments.pop(actor_id, item.token)
            await message.answer("Состояние работы уже изменилось.")
            return
        except Exception:
            LOGGER.exception(
                "WRITERS_MODERATION_COMMENT_FAILED actor_id=%s",
                actor_id,
            )
            await message.answer("Не удалось сохранить решение. Попробуй еще раз.")
            return

        pending_comments.pop(actor_id, item.token)
        escaped_comment = html.escape(comment, quote=True)
        suffix = "" if result.applied else " (решение уже было применено)"
        await message.answer(
            f"✅ Комментарий сохранен: {escaped_comment}{suffix}",
            parse_mode="HTML",
        )

    new_message_handlers = app.dp.message.handlers[message_handler_start:]
    if new_message_handlers:
        del app.dp.message.handlers[message_handler_start:]
        app.dp.message.handlers[0:0] = new_message_handlers

    new_callback_handlers = app.dp.callback_query.handlers[callback_handler_start:]
    if new_callback_handlers:
        del app.dp.callback_query.handlers[callback_handler_start:]
        app.dp.callback_query.handlers[0:0] = new_callback_handlers

    # legacy_main has broad message/callback handlers registered before this
    # subsystem. Promote only our three exact handlers so the deep-link start,
    # moderator callback and pending-comment flow cannot be swallowed first.
    _promote_registered_handler(app.dp.message, writers_submission_start)
    _promote_registered_handler(app.dp.message, writers_delivery_status)
    _promote_registered_handler(app.dp.message, writers_retry_preview)
    _promote_registered_handler(app.dp.message, writers_moderation_comment)
    _promote_registered_handler(app.dp.callback_query, writers_moderation_callback)

    return pending_comments

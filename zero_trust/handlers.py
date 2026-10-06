from __future__ import annotations

import logging
from typing import Any

from aiogram import F
from aiogram.filters import ChatMemberUpdatedFilter, IS_MEMBER, IS_NOT_MEMBER
from aiogram.types import CallbackQuery, ChatMemberUpdated, ChatPermissions

from writers_moderation import (
    RULES_LINK_PREVIEW_OPTIONS,
    build_captcha_success_text,
    build_welcome_text,
)

from .presentation import build_challenge_keyboard, decode_callback
from .service import AnswerKind, ZeroTrustService

LOGGER = logging.getLogger(__name__)


def _restricted_permissions() -> ChatPermissions:
    return ChatPermissions(can_send_messages=False)


def _restored_permissions() -> ChatPermissions:
    return ChatPermissions(
        can_send_messages=True,
        can_send_media_messages=True,
        can_send_other_messages=True,
        can_add_web_page_previews=True,
    )


def _is_writers_chat(writers_scope: Any | None, chat: Any) -> bool:
    return bool(writers_scope is not None and writers_scope.matches(chat))


def _safe_name(app: Any, user: Any) -> str:
    raw = user.full_name or str(user.id)
    formatter = getattr(app, "safe_output_text", None)
    return formatter(raw) if callable(formatter) else raw


def _user_tag(app: Any, user: Any) -> str:
    formatter = getattr(app, "user_tag", None)
    if callable(formatter):
        return formatter(user)
    if getattr(user, "username", None):
        return f"@{user.username}"
    return user.full_name or str(user.id)


def _generic_welcome_text(name: str, question: str) -> str:
    return (
        f"👋 <b>{name}</b>, чтобы получить доступ к чату, реши капчу:\n\n"
        f"<b>{question}</b>"
    )


def register_zero_trust_handlers(
    app: Any,
    service: ZeroTrustService,
    *,
    writers_scope: Any | None = None,
) -> None:
    @app.dp.chat_member(ChatMemberUpdatedFilter(IS_NOT_MEMBER >> IS_MEMBER))
    async def zero_trust_join(event: ChatMemberUpdated) -> None:
        chat_id = int(event.chat.id)
        user = event.new_chat_member.user
        if user.is_bot or not service.is_protected_chat(chat_id):
            return

        # Fail closed: quarantine first. Any subsequent DB/Telegram failure must
        # leave the user restricted rather than silently granting access.
        try:
            await app.bot.restrict_chat_member(
                chat_id,
                int(user.id),
                _restricted_permissions(),
            )
        except Exception:
            LOGGER.exception(
                "ZERO_TRUST_RESTRICT_FAILED chat_id=%s user_id=%s",
                chat_id,
                user.id,
            )
            return

        try:
            challenge, prompt = await service.begin_join(
                chat_id=chat_id,
                user_id=int(user.id),
                username=getattr(user, "username", None),
                display_name=getattr(user, "full_name", None),
            )
        except Exception:
            LOGGER.exception(
                "ZERO_TRUST_CHALLENGE_CREATE_FAILED chat_id=%s user_id=%s",
                chat_id,
                user.id,
            )
            return

        keyboard = build_challenge_keyboard(challenge.id, int(user.id), prompt)
        params: dict[str, Any] = {
            "chat_id": chat_id,
            "reply_markup": keyboard,
        }
        if _is_writers_chat(writers_scope, event.chat):
            params["text"] = build_welcome_text(_safe_name(app, user), prompt.question)
            params["link_preview_options"] = RULES_LINK_PREVIEW_OPTIONS
        else:
            params["text"] = _generic_welcome_text(_safe_name(app, user), prompt.question)

        try:
            sent = await app.bot.send_message(**params)
        except Exception:
            LOGGER.exception(
                "ZERO_TRUST_CHALLENGE_SEND_FAILED challenge_id=%s chat_id=%s user_id=%s",
                challenge.id,
                chat_id,
                user.id,
            )
            return

        message_id = getattr(sent, "message_id", None)
        if message_id is not None:
            try:
                await service.attach_message_id(challenge.id, int(message_id))
            except Exception:
                # The callback already carries challenge_id, so a missing audit
                # message id must not make the challenge fail-open.
                LOGGER.exception(
                    "ZERO_TRUST_MESSAGE_ID_PERSIST_FAILED challenge_id=%s message_id=%s",
                    challenge.id,
                    message_id,
                )

    @app.dp.chat_member(ChatMemberUpdatedFilter(IS_MEMBER >> IS_NOT_MEMBER))
    async def zero_trust_leave(event: ChatMemberUpdated) -> None:
        chat_id = int(event.chat.id)
        user = event.new_chat_member.user
        if user.is_bot or not service.is_protected_chat(chat_id):
            return
        try:
            await service.cancel_leave(chat_id=chat_id, user_id=int(user.id))
        except Exception:
            LOGGER.exception(
                "ZERO_TRUST_LEAVE_CANCEL_FAILED chat_id=%s user_id=%s",
                chat_id,
                user.id,
            )

    @app.dp.callback_query(F.data.startswith("zt:"))
    async def zero_trust_callback(callback: CallbackQuery) -> None:
        if callback.message is None or callback.data is None:
            return

        decoded = decode_callback(callback.data)
        if decoded is None:
            await callback.answer("Некорректная проверка", show_alert=True)
            return
        challenge_id, target_user_id, answer = decoded

        if int(callback.from_user.id) != target_user_id:
            await callback.answer("Это не твоя проверка", show_alert=True)
            return

        chat_id = int(callback.message.chat.id)
        if not service.is_protected_chat(chat_id):
            await callback.answer("Проверка устарела", show_alert=True)
            return

        try:
            result = await service.answer(
                challenge_id=challenge_id,
                chat_id=chat_id,
                user_id=target_user_id,
                answer=answer,
            )
        except Exception:
            LOGGER.exception(
                "ZERO_TRUST_ANSWER_FAILED challenge_id=%s chat_id=%s user_id=%s",
                challenge_id,
                chat_id,
                target_user_id,
            )
            await callback.answer("Проверка временно недоступна", show_alert=True)
            return

        if result.kind is AnswerKind.WRONG:
            await callback.answer("❌ Неверно", show_alert=True)
            return
        if result.kind is AnswerKind.EXPIRED:
            await callback.answer("Проверка истекла", show_alert=True)
            return
        if result.kind is AnswerKind.STALE:
            await callback.answer("Проверка устарела", show_alert=True)
            return

        # VERIFIED and ALREADY_VERIFIED both prove the exact challenge was
        # solved. ALREADY_VERIFIED is the restart/retry path after Telegram or
        # finalization failed previously.
        try:
            await app.bot.restrict_chat_member(
                chat_id,
                target_user_id,
                _restored_permissions(),
            )
        except Exception:
            LOGGER.exception(
                "ZERO_TRUST_ACCESS_RESTORE_FAILED challenge_id=%s chat_id=%s user_id=%s",
                challenge_id,
                chat_id,
                target_user_id,
            )
            await callback.answer(
                "Не удалось вернуть доступ. Попробуй нажать еще раз.",
                show_alert=True,
            )
            return

        try:
            await service.finalize_pass(
                challenge_id=challenge_id,
                chat_id=chat_id,
                user_id=target_user_id,
            )
        except Exception:
            # Telegram access was already restored, but the durable state could
            # not be finalized. Compensate immediately so DB=VERIFIED never
            # coexists with silently granted chat access.
            LOGGER.exception(
                "ZERO_TRUST_FINALIZE_FAILED challenge_id=%s chat_id=%s user_id=%s",
                challenge_id,
                chat_id,
                target_user_id,
            )
            compensated = False
            try:
                await app.bot.restrict_chat_member(
                    chat_id,
                    target_user_id,
                    _restricted_permissions(),
                )
                compensated = True
            except Exception:
                LOGGER.exception(
                    "ZERO_TRUST_FINALIZE_COMPENSATION_FAILED challenge_id=%s chat_id=%s user_id=%s",
                    challenge_id,
                    chat_id,
                    target_user_id,
                )

            if compensated:
                alert = (
                    "Не удалось подтвердить проверку. Доступ снова ограничен; "
                    "попробуй нажать еще раз позже."
                )
            else:
                alert = (
                    "Не удалось подтвердить проверку и повторно ограничить доступ. "
                    "Сообщи администратору."
                )
            await callback.answer(alert, show_alert=True)
            return

        message_thread_id = getattr(callback.message, "message_thread_id", None)
        try:
            await callback.message.delete()
        except Exception:
            LOGGER.warning(
                "Could not delete completed Zero Trust challenge challenge_id=%s",
                challenge_id,
                exc_info=True,
            )

        params: dict[str, Any] = {"chat_id": chat_id}
        if _is_writers_chat(writers_scope, callback.message.chat):
            params["text"] = build_captcha_success_text(_user_tag(app, callback.from_user))
            params["link_preview_options"] = RULES_LINK_PREVIEW_OPTIONS
        else:
            params["text"] = f"✅ {_user_tag(app, callback.from_user)} прошел испытание"
        if message_thread_id is not None:
            params["message_thread_id"] = message_thread_id

        try:
            await app.bot.send_message(**params)
        except Exception:
            LOGGER.exception(
                "ZERO_TRUST_SUCCESS_MESSAGE_FAILED challenge_id=%s chat_id=%s user_id=%s",
                challenge_id,
                chat_id,
                target_user_id,
            )
        await callback.answer("Испытание пройдено")

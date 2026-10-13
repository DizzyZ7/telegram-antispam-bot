from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .config import IKF_SUBSCRIPTION_URL, IKF_SUBSCRIPTION_USERNAME, WritersSubmissionConfig
from .delivery import WritersDeliveryWorker
from .files import WritersFileService
from .handlers import register_writers_submission_handlers
from .membership import WritersCommunityMembershipRegistry, is_current_member
from .menu import install_writers_submission_menu
from .security import SessionSigner
from .service import EligibilityUnavailableError, WritersSubmissionService
from .storage import PostgresWritersSubmissionStorage
from .web import WritersWebServer, create_writers_web_app

LOGGER = logging.getLogger(__name__)


class TelegramWritersEligibilityChecker:
    """Require fresh Telegram membership when submitting, not when drafting."""

    def __init__(
        self, bot: Any, writers_chat_id: int, *,
        registry: WritersCommunityMembershipRegistry | None = None,
        community_join_url: str | None = None,
    ) -> None:
        self.bot = bot
        self.writers_chat_id = writers_chat_id
        self.registry = registry
        self.community_join_url = community_join_url
        self._cached_join_url: str | None = community_join_url
        self._fallback_notified: dict[int, float] = {}

    async def _get_join_url(self) -> str | None:
        if self._cached_join_url:
            return self._cached_join_url
        try:
            chat = await self.bot.get_chat(self.writers_chat_id)
            username = getattr(chat, "username", None)
            if isinstance(username, str) and username:
                link = "https://t.me/" + username.lstrip("@")
            else:
                link = getattr(chat, "invite_link", None)
            if isinstance(link, str) and link.startswith("https://t.me/"):
                self._cached_join_url = link
        except Exception:
            LOGGER.debug("WRITERS_JOIN_LINK_LOOKUP_FAILED", exc_info=True)
        return self._cached_join_url

    async def _friendly_membership_reminder(self, user_id: int) -> None:
        # Prefer a durable once-per-day per-user throttle. A process-local
        # fallback avoids flooding if the membership table is unavailable.
        now = time.time()
        if now - self._fallback_notified.get(user_id, 0) < 24 * 3600:
            return
        if self.registry is not None:
            try:
                if not await self.registry.claim_join_notice(
                    int(self.writers_chat_id), user_id, at=int(now),
                ):
                    return
            except Exception:
                LOGGER.warning("WRITERS_JOIN_REMINDER_DB_FAILED", exc_info=True)
        self._fallback_notified[user_id] = now

        link = await self._get_join_url()
        keyboard = (
            InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(
                        text="💛 Вступить в сообщество ИКФ", url=link,
                    ),
                ]],
            )
            if link else None
        )
        try:
            await self.bot.send_message(
                chat_id=int(user_id),
                text=(
                    "Привет! 💛 Спасибо, что хочешь поделиться своей работой с ИКФ.\n\n"
                    "Заполнять анкету и сохранять ее как черновик можно уже сейчас — "
                    "все останется на месте.\n\n"
                    "Для отправки работы на проверку нужно быть подписчиком канала "
                    "ИКФ. Подпишись, пожалуйста, на @ff_info_ru и затем снова нажми "
                    "«Отправить на проверку» в своей анкете. ✨"
                ),
                reply_markup=keyboard,
            )
        except Exception:
            # Telegram can only DM a user who has already started the bot.
            # The web form independently shows a clear inline message.
            LOGGER.info(
                "WRITERS_JOIN_DM_UNAVAILABLE user_id=%s", user_id,
            )

    async def __call__(self, user_id: int) -> bool:
        user_id = int(user_id)
        try:
            member = await self.bot.get_chat_member(self.writers_chat_id, user_id)
        except Exception as exc:
            LOGGER.warning("WRITERS_ELIGIBILITY_CHECK_FAILED", exc_info=True)
            raise EligibilityUnavailableError(
                "Не удалось проверить участие в сообществе ИКФ. "
                "Повторите отправку позже; черновик сохранен."
            ) from exc

        if self.registry is not None and isinstance(self.writers_chat_id, int):
            try:
                await self.registry.observe(
                    chat_id=self.writers_chat_id,
                    user_id=user_id, member=member, source="lookup",
                )
            except Exception:
                LOGGER.exception("WRITERS_MEMBERSHIP_OBSERVATION_FAILED")

        if is_current_member(member):
            return True
        await self._friendly_membership_reminder(user_id)
        return False

@dataclass(slots=True)
class WritersSubmissionRuntime:
    storage: PostgresWritersSubmissionStorage
    web_server: WritersWebServer
    delivery_worker: WritersDeliveryWorker
    _stopped: bool = False

    async def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True

        first_error: BaseException | None = None
        try:
            await self.delivery_worker.stop()
        except BaseException as exc:  # cleanup must continue through every layer
            first_error = exc
            LOGGER.exception("WRITERS_DELIVERY_STOP_FAILED")

        try:
            await self.web_server.stop()
        except BaseException as exc:
            if first_error is None:
                first_error = exc
            LOGGER.exception("WRITERS_WEB_STOP_FAILED")

        try:
            await self.storage.close()
        except BaseException as exc:
            if first_error is None:
                first_error = exc
            LOGGER.exception("WRITERS_STORAGE_STOP_FAILED")

        if first_error is not None:
            raise first_error


async def start_writers_submission_runtime(
    app: Any,
    config: WritersSubmissionConfig,
    *,
    database_url: str | None,
    bot_token: str | None,
) -> WritersSubmissionRuntime | None:
    if not bool(config.enabled):
        return None

    database_url = (database_url or "").strip()
    bot_token = (bot_token or "").strip()
    if not database_url.startswith(("postgresql://", "postgres://")):
        raise RuntimeError(
            "Writers Submission requires a PostgreSQL DATABASE_URL"
        )
    if not bot_token:
        raise RuntimeError("Writers Submission requires BOT_TOKEN")
    if config.writers_chat_id is None:
        raise RuntimeError("Writers Submission requires WRITERS_CHAT_ID")

    storage = PostgresWritersSubmissionStorage(database_url)
    web_server: WritersWebServer | None = None
    delivery_worker: WritersDeliveryWorker | None = None
    worker_start_attempted = False

    try:
        await storage.initialize()

        # Resolve the exact public IKF channel. Never fall back to the
        # discussion-group WRITERS_CHAT_ID: it belongs to Zero Trust, not
        # the publication subscription gate. If Telegram is temporarily
        # unavailable, direct @username membership checks still fail closed.
        community_chat_id: int | str = IKF_SUBSCRIPTION_USERNAME
        verified_channel_id: int | None = None
        try:
            channel = await app.bot.get_chat(IKF_SUBSCRIPTION_USERNAME)
            channel_type = getattr(channel, "type", "")
            channel_type = str(getattr(channel_type, "value", channel_type)).casefold()
            channel_username = str(getattr(channel, "username", "") or "").lstrip("@").casefold()
            resolved_id = int(channel.id)
            if channel_type == "channel" and channel_username == "ff_info_ru" and resolved_id < 0:
                verified_channel_id = resolved_id
                community_chat_id = resolved_id
                hint = getattr(config, "membership_chat_id", None)
                if hint is not None and int(hint) != resolved_id:
                    LOGGER.warning(
                        "WRITERS_CHANNEL_ID_HINT_IGNORED configured=%s actual=%s",
                        hint, resolved_id,
                    )
            else:
                LOGGER.error(
                    "WRITERS_IKF_CHANNEL_UNVERIFIED username=%s type=%s",
                    channel_username, channel_type,
                )
        except Exception:
            LOGGER.warning("WRITERS_IKF_CHANNEL_RESOLVE_FAILED", exc_info=True)
        registry = WritersCommunityMembershipRegistry(storage)
        await registry.initialize()
        eligibility = TelegramWritersEligibilityChecker(
            app.bot,
            community_chat_id,
            registry=registry if verified_channel_id is not None else None,
            community_join_url=IKF_SUBSCRIPTION_URL,
        )
        service = WritersSubmissionService(
            storage,
            config,
            eligibility,
        )
        file_service = WritersFileService(
            app.bot,
            storage,
            config,
        )
        session_signer = SessionSigner(
            bot_token,
            ttl_seconds=int(config.session_ttl_seconds),
        )
        web_app = create_writers_web_app(
            service,
            file_service,
            config,
            session_signer,
            bot_token=bot_token,
        )
        web_server = WritersWebServer(
            web_app,
            host=str(config.bind_host),
            port=int(config.port),
        )
        delivery_worker = WritersDeliveryWorker(
            app.bot,
            storage,
            config,
        )

        # Bind before handlers are exposed to polling. If the port is unusable,
        # startup aborts without activating the Telegram-facing feature.
        await web_server.start()

        register_writers_submission_handlers(
            app,
            service,
            config,
            membership_registry=registry,
            membership_chat_id=verified_channel_id,
        )

        worker_start_attempted = True
        await delivery_worker.start()

        # The Telegram chat-menu button is bot-wide and appears beside the
        # composer in private chats, without needing /start writers_submit.
        # Publish it only after HTTP, handlers and delivery are ready.
        # A Telegram API outage must not disable an otherwise working bot:
        # the existing deep-link and inline button remain available.
        try:
            await install_writers_submission_menu(
                app.bot,
                public_url=str(config.public_url),
            )
        except Exception:
            LOGGER.exception("WRITERS_MENU_BUTTON_SETUP_FAILED")
        else:
            LOGGER.info("WRITERS_MENU_BUTTON_READY")

        LOGGER.info(
            "WRITERS_MODERATION_ROUTE_READY mode=%s destination_chat_id=%s reviewers=%s",
            getattr(config, "moderation_mode", "group"),
            getattr(config, "moderation_chat_id", None),
            ",".join(
                str(value)
                for value in sorted(getattr(config, "moderator_ids", ()))
            ),
        )
        LOGGER.info(
            "WRITERS_SUBMISSION_READY bind=%s:%s",
            config.bind_host,
            config.port,
        )
        return WritersSubmissionRuntime(
            storage=storage,
            web_server=web_server,
            delivery_worker=delivery_worker,
        )
    except BaseException:
        # Startup is all-or-nothing. Polling is owned by main.py and has not
        # started yet, so cleaning these resources is sufficient to fail closed.
        if delivery_worker is not None and worker_start_attempted:
            try:
                await delivery_worker.stop()
            except BaseException:
                LOGGER.exception("WRITERS_DELIVERY_STARTUP_CLEANUP_FAILED")
        if web_server is not None:
            try:
                await web_server.stop()
            except BaseException:
                LOGGER.exception("WRITERS_WEB_STARTUP_CLEANUP_FAILED")
        try:
            await storage.close()
        except BaseException:
            LOGGER.exception("WRITERS_STORAGE_STARTUP_CLEANUP_FAILED")
        raise

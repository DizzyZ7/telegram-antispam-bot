from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .config import WritersSubmissionConfig
from .delivery import WritersDeliveryWorker
from .files import WritersFileService
from .handlers import register_writers_submission_handlers
from .menu import install_writers_submission_menu
from .models import EligibilityUnavailableError
from .security import SessionSigner
from .service import WritersSubmissionService
from .storage import PostgresWritersSubmissionStorage
from .web import WritersWebServer, create_writers_web_app

LOGGER = logging.getLogger(__name__)


class TelegramWritersEligibilityChecker:
    def __init__(self, bot: Any, writers_chat_id: int, storage: Any = None) -> None:
        self.bot = bot
        self.writers_chat_id = int(writers_chat_id)
        self.storage = storage

    async def __call__(self, user_id: int) -> bool:
        try:
            member = await self.bot.get_chat_member(
                self.writers_chat_id,
                int(user_id),
            )
        except Exception as exc:
            # Don't misreport Telegram outages or missing bot permissions as
            # "not a member". Keep submission blocked until access is verified.
            LOGGER.warning(
                "WRITERS_ELIGIBILITY_CHECK_UNAVAILABLE chat_id=%s error_type=%s",
                self.writers_chat_id, type(exc).__name__,
            )
            raise EligibilityUnavailableError("Telegram membership check unavailable") from exc

        raw_status = getattr(member, "status", "")
        status = str(getattr(raw_status, "value", raw_status)).casefold()
        is_member = (
            getattr(member, "is_member", False) is True
            if status == "restricted"
            else status in {"member", "administrator", "creator", "owner"}
        )
        if self.storage is not None:
            try:
                await self.storage.record_community_member(
                    chat_id=self.writers_chat_id,
                    user_id=int(user_id),
                    status=status,
                    is_member=is_member,
                    source="live_lookup",
                    now=int(time.time()),
                )
            except Exception:
                # A database telemetry outage cannot bypass or invalidate
                # the *live* membership decision returned by Telegram.
                LOGGER.exception("WRITERS_MEMBERSHIP_OBSERVATION_FAILED")
        return is_member


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

        membership_chat_id = int(
            getattr(config, "membership_chat_id", None) or config.writers_chat_id
        )
        eligibility = TelegramWritersEligibilityChecker(
            app.bot,
            membership_chat_id,
            storage=storage,
        )
        reminder_sent_at: dict[int, float] = {}

        async def invite_nonmember(user_id: int) -> None:
            now = time.monotonic()
            if now - reminder_sent_at.get(user_id, -1e9) < 15 * 60:
                return
            reminder_sent_at[user_id] = now
            community_url = getattr(config, "community_url", None)
            if not community_url:
                try:
                    chat = await app.bot.get_chat(membership_chat_id)
                    username = getattr(chat, "username", None)
                    invite_link = getattr(chat, "invite_link", None)
                    if username:
                        community_url = "https://t.me/" + str(username).lstrip("@")
                    elif isinstance(invite_link, str) and invite_link.startswith(
                        ("https://t.me/", "https://telegram.me/")
                    ):
                        community_url = invite_link
                except Exception:
                    LOGGER.info("WRITERS_COMMUNITY_URL_UNAVAILABLE chat_id=%s", membership_chat_id)
            buttons = (
                InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="🌿 Вступить в ИКФ", url=community_url),
                ]])
                if community_url else None
            )
            try:
                await app.bot.send_message(
                    chat_id=int(user_id),
                    text=(
                        "Привет! 💛 Спасибо, что поделился своей работой с ИКФ. "
                        "Анкета уже сохранена в черновиках — ничего переписывать не нужно.\n\n"
                        "Чтобы отправить ее на проверку, сначала вступи в наше сообщество ИКФ. "
                        "После вступления открой черновик и снова нажми «Отправить на проверку». "
                        "Будем рады видеть тебя с нами! ✨"
                    ),
                    reply_markup=buttons,
                )
            except Exception:
                # A bot cannot initiate a conversation with someone who has
                # never opened its private chat. The Mini App also explains
                # this requirement; a failed DM never destroys the draft.
                LOGGER.info("WRITERS_COMMUNITY_DM_NOT_DELIVERED user_id=%s", user_id)

        service = WritersSubmissionService(
            storage, config, eligibility, nonmember_notifier=invite_nonmember,
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

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .config import WritersSubmissionConfig
from .delivery import WritersDeliveryWorker
from .files import WritersFileService
from .handlers import register_writers_submission_handlers
from .menu import install_writers_submission_menu
from .security import SessionSigner
from .service import WritersSubmissionService
from .storage import PostgresWritersSubmissionStorage
from .web import WritersWebServer, create_writers_web_app

LOGGER = logging.getLogger(__name__)


class TelegramWritersEligibilityChecker:
    def __init__(self, bot: Any, writers_chat_id: int) -> None:
        self.bot = bot
        self.writers_chat_id = int(writers_chat_id)

    async def __call__(self, user_id: int) -> bool:
        try:
            member = await self.bot.get_chat_member(
                self.writers_chat_id,
                int(user_id),
            )
        except Exception:
            # Eligibility gates creation/submission, so Telegram uncertainty
            # fails closed instead of admitting an unverifiable actor.
            LOGGER.warning("WRITERS_ELIGIBILITY_CHECK_FAILED")
            return False

        raw_status = getattr(member, "status", "")
        status = str(getattr(raw_status, "value", raw_status)).casefold()
        return status in {"member", "administrator", "creator"}


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

        eligibility = TelegramWritersEligibilityChecker(
            app.bot,
            int(config.writers_chat_id),
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
            config.moderation_chat_id,
            ",".join(str(value) for value in sorted(config.moderator_ids)),
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

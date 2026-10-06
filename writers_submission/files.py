from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from uuid import UUID

from aiogram.types import FSInputFile

from .models import SubmissionFile, ValidationError
from .uploads import validate_staged_file

LOGGER = logging.getLogger(__name__)


class WritersFileService:
    def __init__(
        self,
        bot: Any,
        storage: Any,
        config: Any,
    ) -> None:
        self.bot = bot
        self.storage = storage
        self.config = config

    async def attach_from_temp(
        self,
        *,
        author_user_id: int,
        submission_id: UUID,
        temp_path: Path,
        original_filename: str,
        declared_mime: str,
        now: int,
    ) -> SubmissionFile:
        path = Path(temp_path)
        staging_message = None
        try:
            upload = validate_staged_file(
                path,
                filename=original_filename,
                declared_mime=declared_mime,
                max_bytes=int(self.config.max_file_bytes),
            )
            context = await self.storage.get_draft_file_context(
                submission_id=submission_id,
                author_user_id=int(author_user_id),
            )
            if int(context.file_count) >= int(self.config.max_files):
                raise ValidationError("maximum file count reached")

            staging_message = await self.bot.send_document(
                chat_id=int(self.config.file_chat_id),
                document=FSInputFile(
                    path,
                    filename=upload.safe_filename,
                ),
                disable_notification=True,
            )
            document = getattr(staging_message, "document", None)
            telegram_file_id = getattr(document, "file_id", None)
            if not telegram_file_id:
                raise RuntimeError("Telegram staging response has no document file_id")

            try:
                return await self.storage.add_ready_file(
                    submission_id=submission_id,
                    author_user_id=int(author_user_id),
                    revision_id=context.revision_id,
                    upload=upload,
                    telegram_file_id=str(telegram_file_id),
                    telegram_file_unique_id=(
                        str(document.file_unique_id)
                        if getattr(document, "file_unique_id", None)
                        else None
                    ),
                    storage_chat_id=int(self.config.file_chat_id),
                    storage_message_id=int(staging_message.message_id),
                    max_files=int(self.config.max_files),
                    now=int(now),
                )
            except Exception:
                try:
                    await self.bot.delete_message(
                        chat_id=int(self.config.file_chat_id),
                        message_id=int(staging_message.message_id),
                    )
                except Exception as cleanup_exc:
                    LOGGER.warning(
                        "WRITERS_FILE_STAGING_CLEANUP_FAILED message_id=%s error=%s",
                        getattr(staging_message, "message_id", None),
                        type(cleanup_exc).__name__,
                    )
                raise
        finally:
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                LOGGER.warning(
                    "WRITERS_FILE_TEMP_CLEANUP_FAILED error=%s",
                    type(exc).__name__,
                )

    async def delete_attachment(
        self,
        *,
        author_user_id: int,
        submission_id: UUID,
        file_id: UUID,
        now: int,
    ) -> None:
        deleted = await self.storage.delete_ready_file(
            submission_id=submission_id,
            author_user_id=int(author_user_id),
            file_id=file_id,
            now=int(now),
        )
        if (
            deleted.storage_chat_id is not None
            and deleted.storage_message_id is not None
        ):
            try:
                await self.bot.delete_message(
                    chat_id=int(deleted.storage_chat_id),
                    message_id=int(deleted.storage_message_id),
                )
            except Exception as exc:
                LOGGER.warning(
                    "WRITERS_FILE_STORAGE_MESSAGE_CLEANUP_FAILED file_id=%s error=%s",
                    file_id,
                    type(exc).__name__,
                )

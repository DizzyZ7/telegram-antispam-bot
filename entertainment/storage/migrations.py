"""One-time import of v1 entertainment SQLite data into the selected v2 backend."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiosqlite

from .sqlite import SQLiteEntertainmentStorage

MIGRATION_KEY = "entertainment_v1_sqlite_import"


@dataclass(frozen=True, slots=True)
class MigrationReport:
    settings_imported: int = 0
    messages_imported: int = 0
    skipped: int = 0
    already_applied: bool = False


async def _table_exists(connection: aiosqlite.Connection, table_name: str) -> bool:
    async with connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ) as cursor:
        return await cursor.fetchone() is not None


async def migrate_v1_sqlite_if_needed(
    source_path: Path,
    target: Any,
) -> MigrationReport:
    source_path = Path(source_path)
    if not source_path.is_file():
        return MigrationReport()

    if isinstance(target, SQLiteEntertainmentStorage):
        try:
            if target.database_path.resolve() == source_path.resolve():
                return MigrationReport(already_applied=True)
        except OSError:
            pass

    if await target.is_migration_applied(MIGRATION_KEY):
        return MigrationReport(already_applied=True)

    source = await aiosqlite.connect(source_path)
    try:
        has_settings = await _table_exists(source, "entertainment_chat_settings")
        has_messages = await _table_exists(source, "entertainment_messages")
        if not has_settings and not has_messages:
            return MigrationReport()

        settings_rows: list[tuple[int, bool, int, int, int]] = []
        message_rows: list[tuple[int, int, int, str, int]] = []

        if has_settings:
            async with source.execute(
                """
                SELECT chat_id, enabled, laziness, cooldown_seconds, updated_at
                FROM entertainment_chat_settings
                ORDER BY chat_id
                """
            ) as cursor:
                for row in await cursor.fetchall():
                    settings_rows.append(
                        (int(row[0]), bool(row[1]), int(row[2]), int(row[3]), int(row[4]))
                    )

        if has_messages:
            async with source.execute(
                """
                SELECT id, chat_id, user_id, text, created_at
                FROM entertainment_messages
                ORDER BY id
                """
            ) as cursor:
                for row in await cursor.fetchall():
                    message_rows.append(
                        (int(row[0]), int(row[1]), int(row[2]), str(row[3]), int(row[4]))
                    )

        settings_imported, messages_imported = await target.import_legacy_batch(
            MIGRATION_KEY,
            settings_rows,
            message_rows,
        )
        total = len(settings_rows) + len(message_rows)
        imported = settings_imported + messages_imported
        return MigrationReport(
            settings_imported=settings_imported,
            messages_imported=messages_imported,
            skipped=max(0, total - imported),
            already_applied=False,
        )
    finally:
        await source.close()

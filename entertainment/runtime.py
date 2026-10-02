"""Production lifecycle helpers for entertainment persistence."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import EntertainmentDatabaseConfig
from .storage.base import EntertainmentStorage
from .storage.factory import open_entertainment_storage
from .storage.migrations import MigrationReport, migrate_v1_sqlite_if_needed
from .storage.postgres import PostgresEntertainmentStorage

CULTURE_MEMORY_BACKFILL_KEY = "culture_memory_v1_text_backfill"


@dataclass(frozen=True, slots=True)
class EntertainmentRuntimeStorage:
    storage: EntertainmentStorage
    backend: str
    migration: MigrationReport
    culture_events_imported: int = 0


async def open_entertainment_runtime_storage(data_dir: Path) -> EntertainmentRuntimeStorage:
    """Open configured storage and apply non-destructive Entertainment migrations.

    Both the legacy SQLite->configured-backend migration and the canonical
    Culture Memory text backfill complete before handlers start. Any migration
    failure closes the storage before re-raising so the process cannot continue
    with partially initialized memory.
    """
    config = EntertainmentDatabaseConfig.from_env(Path(data_dir))
    storage = await open_entertainment_storage(config)
    try:
        migration = await migrate_v1_sqlite_if_needed(config.sqlite_path, storage)
        culture_events_imported = await storage.backfill_legacy_memory(
            CULTURE_MEMORY_BACKFILL_KEY
        )
    except BaseException:
        await storage.close()
        raise

    backend = "postgres" if isinstance(storage, PostgresEntertainmentStorage) else "sqlite"
    print(
        "ENTERTAINMENT_CULTURE_MEMORY_READY "
        f"backend={backend} backfill_events={int(culture_events_imported)}",
        flush=True,
    )
    return EntertainmentRuntimeStorage(
        storage=storage,
        backend=backend,
        migration=migration,
        culture_events_imported=int(culture_events_imported),
    )

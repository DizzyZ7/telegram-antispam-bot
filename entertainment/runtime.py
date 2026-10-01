"""Production lifecycle helpers for entertainment persistence."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import EntertainmentDatabaseConfig
from .storage.base import EntertainmentStorage
from .storage.factory import open_entertainment_storage
from .storage.migrations import MigrationReport, migrate_v1_sqlite_if_needed
from .storage.postgres import PostgresEntertainmentStorage


@dataclass(frozen=True, slots=True)
class EntertainmentRuntimeStorage:
    storage: EntertainmentStorage
    backend: str
    migration: MigrationReport


async def open_entertainment_runtime_storage(data_dir: Path) -> EntertainmentRuntimeStorage:
    """Open configured storage and safely migrate old local entertainment data.

    The connection is closed before re-raising if migration fails so a partial
    application startup cannot leak a PostgreSQL pool or SQLite connection.
    """
    config = EntertainmentDatabaseConfig.from_env(Path(data_dir))
    storage = await open_entertainment_storage(config)
    try:
        migration = await migrate_v1_sqlite_if_needed(config.sqlite_path, storage)
    except BaseException:
        await storage.close()
        raise

    backend = "postgres" if isinstance(storage, PostgresEntertainmentStorage) else "sqlite"
    return EntertainmentRuntimeStorage(storage=storage, backend=backend, migration=migration)

"""Open the configured entertainment persistence backend."""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

from ..config import EntertainmentDatabaseConfig
from .base import EntertainmentStorage
from .postgres import PostgresEntertainmentStorage
from .sqlite import SQLiteEntertainmentStorage

LOGGER = logging.getLogger(__name__)


class EntertainmentStorageConfigurationError(RuntimeError):
    """Raised when entertainment database configuration is unsupported."""


class EntertainmentStorageUnavailableError(RuntimeError):
    """Raised when the configured production backend cannot be initialized."""


async def _open_sqlite(config: EntertainmentDatabaseConfig) -> SQLiteEntertainmentStorage:
    storage = SQLiteEntertainmentStorage(config.sqlite_path)
    await storage.initialize()
    return storage


async def open_entertainment_storage(
    config: EntertainmentDatabaseConfig,
) -> EntertainmentStorage:
    if not config.database_url:
        return await _open_sqlite(config)

    scheme = urlsplit(config.database_url).scheme.casefold()
    if scheme not in {"postgres", "postgresql"}:
        raise EntertainmentStorageConfigurationError(
            f"Unsupported entertainment DATABASE_URL scheme: {scheme or '<missing>'}"
        )

    storage = PostgresEntertainmentStorage(config.database_url)
    try:
        await storage.initialize()
        return storage
    except Exception as exc:
        try:
            await storage.close()
        except Exception:
            LOGGER.exception("Could not close failed entertainment PostgreSQL pool")

        if not config.allow_sqlite_fallback:
            raise EntertainmentStorageUnavailableError(
                "Configured entertainment PostgreSQL database is unavailable; "
                "SQLite fallback is disabled"
            ) from exc

        LOGGER.warning(
            "ENTERTAINMENT_DB_POSTGRES_UNAVAILABLE falling_back=sqlite explicit_fallback=1"
        )
        try:
            return await _open_sqlite(config)
        except Exception as fallback_exc:
            raise EntertainmentStorageUnavailableError(
                "PostgreSQL and explicit SQLite fallback both failed"
            ) from fallback_exc


__all__ = [
    "EntertainmentDatabaseConfig",
    "EntertainmentStorageConfigurationError",
    "EntertainmentStorageUnavailableError",
    "open_entertainment_storage",
]

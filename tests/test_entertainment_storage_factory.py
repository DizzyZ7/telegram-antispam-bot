from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from entertainment.storage.factory import (
    EntertainmentDatabaseConfig,
    EntertainmentStorageConfigurationError,
    EntertainmentStorageUnavailableError,
    open_entertainment_storage,
)
from entertainment.storage.postgres import PostgresEntertainmentStorage
from entertainment.storage.sqlite import SQLiteEntertainmentStorage


class EntertainmentDatabaseConfigTests(unittest.TestCase):
    def test_from_env_without_database_url_uses_data_dir_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict("os.environ", {}, clear=True):
                config = EntertainmentDatabaseConfig.from_env(Path(temp_dir))

        self.assertIsNone(config.database_url)
        self.assertEqual(config.sqlite_path, Path(temp_dir) / "entertainment.db")
        self.assertFalse(config.allow_sqlite_fallback)

    def test_from_env_reads_explicit_fallback_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(
                "os.environ",
                {
                    "DATABASE_URL": "postgresql://user:pass@db.example/bot",
                    "ENTERTAINMENT_DB_FALLBACK_SQLITE": "1",
                },
                clear=True,
            ):
                config = EntertainmentDatabaseConfig.from_env(Path(temp_dir))

        self.assertTrue(config.allow_sqlite_fallback)
        self.assertEqual(config.database_url, "postgresql://user:pass@db.example/bot")


class EntertainmentStorageFactoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_database_url_opens_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = EntertainmentDatabaseConfig(
                database_url=None,
                sqlite_path=Path(temp_dir) / "entertainment.db",
                allow_sqlite_fallback=False,
            )
            storage = await open_entertainment_storage(config)
            try:
                self.assertIsInstance(storage, SQLiteEntertainmentStorage)
            finally:
                await storage.close()

    async def test_postgresql_scheme_opens_postgres(self) -> None:
        config = EntertainmentDatabaseConfig(
            database_url="postgresql://user:pass@localhost/db",
            sqlite_path=Path("unused.db"),
            allow_sqlite_fallback=False,
        )
        with patch.object(PostgresEntertainmentStorage, "initialize", new=AsyncMock()) as initialize:
            storage = await open_entertainment_storage(config)

        self.assertIsInstance(storage, PostgresEntertainmentStorage)
        initialize.assert_awaited_once()

    async def test_postgres_alias_scheme_is_supported(self) -> None:
        config = EntertainmentDatabaseConfig(
            database_url="postgres://user:pass@localhost/db",
            sqlite_path=Path("unused.db"),
            allow_sqlite_fallback=False,
        )
        with patch.object(PostgresEntertainmentStorage, "initialize", new=AsyncMock()):
            storage = await open_entertainment_storage(config)
        self.assertIsInstance(storage, PostgresEntertainmentStorage)

    async def test_unsupported_database_scheme_is_rejected(self) -> None:
        config = EntertainmentDatabaseConfig(
            database_url="mysql://user:pass@localhost/db",
            sqlite_path=Path("unused.db"),
            allow_sqlite_fallback=False,
        )
        with self.assertRaises(EntertainmentStorageConfigurationError):
            await open_entertainment_storage(config)

    async def test_postgres_failure_is_fail_fast_by_default(self) -> None:
        config = EntertainmentDatabaseConfig(
            database_url="postgresql://user:pass@localhost/db",
            sqlite_path=Path("unused.db"),
            allow_sqlite_fallback=False,
        )
        with patch.object(
            PostgresEntertainmentStorage,
            "initialize",
            new=AsyncMock(side_effect=OSError("database unavailable")),
        ):
            with self.assertRaises(EntertainmentStorageUnavailableError):
                await open_entertainment_storage(config)

    async def test_explicit_fallback_opens_sqlite_after_postgres_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = EntertainmentDatabaseConfig(
                database_url="postgresql://user:pass@localhost/db",
                sqlite_path=Path(temp_dir) / "entertainment.db",
                allow_sqlite_fallback=True,
            )
            with patch.object(
                PostgresEntertainmentStorage,
                "initialize",
                new=AsyncMock(side_effect=OSError("database unavailable")),
            ):
                storage = await open_entertainment_storage(config)
            try:
                self.assertIsInstance(storage, SQLiteEntertainmentStorage)
            finally:
                await storage.close()


if __name__ == "__main__":
    unittest.main()

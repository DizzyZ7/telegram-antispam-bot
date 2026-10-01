from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from entertainment.runtime import open_entertainment_runtime_storage
from entertainment.storage.sqlite import SQLiteEntertainmentStorage


class EntertainmentRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_sqlite_runtime_opens_without_database_url(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            with patch.dict("os.environ", {}, clear=True):
                result = await open_entertainment_runtime_storage(data_dir)
            try:
                self.assertIsInstance(result.storage, SQLiteEntertainmentStorage)
                self.assertEqual(result.backend, "sqlite")
                self.assertTrue((data_dir / "entertainment.db").exists())
            finally:
                await result.storage.close()

    async def test_storage_is_closed_when_migration_fails(self) -> None:
        storage = AsyncMock()
        storage.close = AsyncMock()
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch(
                "entertainment.runtime.open_entertainment_storage",
                new=AsyncMock(return_value=storage),
            ), patch(
                "entertainment.runtime.migrate_v1_sqlite_if_needed",
                new=AsyncMock(side_effect=RuntimeError("migration failed")),
            ):
                with self.assertRaisesRegex(RuntimeError, "migration failed"):
                    await open_entertainment_runtime_storage(Path(temp_dir))
        storage.close.assert_awaited_once()

    async def test_runtime_reports_postgres_without_exposing_database_url(self) -> None:
        from entertainment.storage.postgres import PostgresEntertainmentStorage

        storage = object.__new__(PostgresEntertainmentStorage)
        storage.database_url = "postgresql://secret:secret@db/production"
        storage.close = AsyncMock()  # type: ignore[method-assign]
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch(
                "entertainment.runtime.open_entertainment_storage",
                new=AsyncMock(return_value=storage),
            ), patch(
                "entertainment.runtime.migrate_v1_sqlite_if_needed",
                new=AsyncMock(),
            ):
                result = await open_entertainment_runtime_storage(Path(temp_dir))
        self.assertEqual(result.backend, "postgres")
        self.assertNotIn("secret", result.backend)


if __name__ == "__main__":
    unittest.main()

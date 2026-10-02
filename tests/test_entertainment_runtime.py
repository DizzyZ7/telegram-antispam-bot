from __future__ import annotations

import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from entertainment.runtime import open_entertainment_runtime_storage
from entertainment.storage.sqlite import SQLiteEntertainmentStorage


class EntertainmentRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_sqlite_runtime_opens_without_database_url_and_backfills_culture_memory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            output = StringIO()
            with patch.dict("os.environ", {}, clear=True), redirect_stdout(output):
                result = await open_entertainment_runtime_storage(data_dir)
            try:
                self.assertIsInstance(result.storage, SQLiteEntertainmentStorage)
                self.assertEqual(result.backend, "sqlite")
                self.assertEqual(result.culture_events_imported, 0)
                self.assertTrue((data_dir / "entertainment.db").exists())
                self.assertIn("ENTERTAINMENT_CULTURE_MEMORY_READY backfill_events=0", output.getvalue())
            finally:
                await result.storage.close()

    async def test_storage_is_closed_when_legacy_migration_fails(self) -> None:
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

    async def test_storage_is_closed_when_culture_backfill_fails(self) -> None:
        storage = AsyncMock()
        storage.close = AsyncMock()
        storage.backfill_legacy_memory = AsyncMock(side_effect=RuntimeError("culture backfill failed"))
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch(
                "entertainment.runtime.open_entertainment_storage",
                new=AsyncMock(return_value=storage),
            ), patch(
                "entertainment.runtime.migrate_v1_sqlite_if_needed",
                new=AsyncMock(return_value=SimpleNamespace()),
            ):
                with self.assertRaisesRegex(RuntimeError, "culture backfill failed"):
                    await open_entertainment_runtime_storage(Path(temp_dir))
        storage.close.assert_awaited_once()

    async def test_runtime_reports_backfill_count_without_exposing_database_url(self) -> None:
        from entertainment.storage.postgres import PostgresEntertainmentStorage

        storage = object.__new__(PostgresEntertainmentStorage)
        storage.database_url = "postgresql://secret:secret@db/production"
        storage.close = AsyncMock()  # type: ignore[method-assign]
        storage.backfill_legacy_memory = AsyncMock(return_value=12)  # type: ignore[attr-defined]
        output = StringIO()
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch(
                "entertainment.runtime.open_entertainment_storage",
                new=AsyncMock(return_value=storage),
            ), patch(
                "entertainment.runtime.migrate_v1_sqlite_if_needed",
                new=AsyncMock(return_value=SimpleNamespace()),
            ), redirect_stdout(output):
                result = await open_entertainment_runtime_storage(Path(temp_dir))
        self.assertEqual(result.backend, "postgres")
        self.assertEqual(result.culture_events_imported, 12)
        self.assertIn("backfill_events=12", output.getvalue())
        self.assertNotIn("secret", output.getvalue())


if __name__ == "__main__":
    unittest.main()

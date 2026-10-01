from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from entertainment.storage import SQLiteEntertainmentStorage


class EntertainmentMemoryConfigTests(unittest.TestCase):
    def _probe_config(self, **overrides: str) -> tuple[int, int, int]:
        env = os.environ.copy()
        for key in (
            "ENTERTAINMENT_MEMORY_LIMIT",
            "ENTERTAINMENT_GENERATION_SAMPLE_LIMIT",
            "ENTERTAINMENT_MEMORY_PRUNE_BUFFER",
        ):
            env.pop(key, None)
        env.update(overrides)
        output = subprocess.check_output(
            [
                sys.executable,
                "-c",
                (
                    "from entertainment.config import MEMORY_LIMIT, GENERATION_SAMPLE_LIMIT, "
                    "MEMORY_PRUNE_BUFFER; "
                    "print(MEMORY_LIMIT, GENERATION_SAMPLE_LIMIT, MEMORY_PRUNE_BUFFER)"
                ),
            ],
            env=env,
            text=True,
        )
        return tuple(int(part) for part in output.strip().split())  # type: ignore[return-value]

    def test_defaults_favor_long_term_postgres_memory(self) -> None:
        self.assertEqual(self._probe_config(), (100_000, 1_500, 1_000))

    def test_limits_can_be_overridden_by_environment(self) -> None:
        self.assertEqual(
            self._probe_config(
                ENTERTAINMENT_MEMORY_LIMIT="250000",
                ENTERTAINMENT_GENERATION_SAMPLE_LIMIT="2400",
                ENTERTAINMENT_MEMORY_PRUNE_BUFFER="500",
            ),
            (250_000, 2_400, 500),
        )

    def test_invalid_values_fall_back_to_safe_defaults(self) -> None:
        self.assertEqual(
            self._probe_config(
                ENTERTAINMENT_MEMORY_LIMIT="oops",
                ENTERTAINMENT_GENERATION_SAMPLE_LIMIT="-1",
                ENTERTAINMENT_MEMORY_PRUNE_BUFFER="0",
            ),
            (100_000, 1_500, 1_000),
        )


class SQLiteEntertainmentMemoryRetentionTests(unittest.TestCase):
    def test_prunes_in_batches_instead_of_on_every_message(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as tmpdir:
                storage = SQLiteEntertainmentStorage(
                    Path(tmpdir) / "entertainment.db",
                    memory_limit=3,
                    prune_buffer=2,
                )
                await storage.initialize()
                try:
                    for index in range(5):
                        await storage.add_message(-1001, 7, index + 1, f"message-{index}")
                    self.assertEqual(await storage.message_count(-1001, 7), 5)

                    await storage.add_message(-1001, 7, 99, "trigger-prune")
                    self.assertEqual(await storage.message_count(-1001, 7), 3)
                    self.assertEqual(
                        await storage.recent_messages(-1001, 7, limit=10),
                        ["message-3", "message-4", "trigger-prune"],
                    )
                finally:
                    await storage.close()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()

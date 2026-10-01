from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime_env import load_runtime_env


class RuntimeEnvTests(unittest.TestCase):
    def test_dotenv_loads_missing_database_url_without_overriding_existing_env(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app_dir = Path(temp_dir)
            (app_dir / ".env").write_text(
                "DATABASE_URL=postgresql://example.invalid/test\n"
                "ENTERTAINMENT_CHAT_IDS=-100123\n",
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"ENTERTAINMENT_CHAT_IDS": "-100999"},
                clear=True,
            ):
                loaded = load_runtime_env(app_dir)
                self.assertTrue(loaded)
                self.assertEqual(
                    os.environ["DATABASE_URL"],
                    "postgresql://example.invalid/test",
                )
                self.assertEqual(os.environ["ENTERTAINMENT_CHAT_IDS"], "-100999")

    def test_missing_dotenv_is_a_noop(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {}, clear=True):
                self.assertFalse(load_runtime_env(Path(temp_dir)))
                self.assertNotIn("DATABASE_URL", os.environ)


if __name__ == "__main__":
    unittest.main()

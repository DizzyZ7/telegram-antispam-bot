"""Guard against Bothost trying to execute this Python app with Node.js."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class BothostDeploymentContractTests(unittest.TestCase):
    def test_archive_manifest_selects_python_entrypoint(self):
        manifest = json.loads((ROOT / "bothost.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["language"], "python")
        self.assertEqual(manifest["main"], "main.py")
        self.assertTrue((ROOT / manifest["main"]).is_file())
        self.assertTrue((ROOT / "requirements.txt").is_file())

    def test_custom_image_runs_python_not_browser_javascript(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("FROM python:3.12-slim", dockerfile)
        self.assertIn("CMD [\"python\", \"main.py\"]", dockerfile)
        self.assertIn("EXPOSE 3000", dockerfile)
        self.assertNotIn('CMD ["node",', dockerfile)


if __name__ == "__main__":
    unittest.main()

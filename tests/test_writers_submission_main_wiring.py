from __future__ import annotations

import unittest
from pathlib import Path


class WritersSubmissionMainWiringTests(unittest.TestCase):
    def test_production_entrypoint_starts_and_stops_writers_runtime(self):
        source = Path("main.py").read_text(encoding="utf-8")

        self.assertIn(
            "from writers_submission.config import WritersSubmissionConfig",
            source,
        )
        self.assertIn(
            "from writers_submission.runtime import start_writers_submission_runtime",
            source,
        )
        self.assertIn("writers_submission_runtime = None", source)
        self.assertIn("WritersSubmissionConfig.from_env(", source)
        self.assertIn("await start_writers_submission_runtime(", source)
        self.assertIn("await writers_submission_runtime.stop()", source)

        start_index = source.index("await start_writers_submission_runtime(")
        polling_index = source.index("await app.main()")
        self.assertLess(start_index, polling_index)

        stop_index = source.index("await writers_submission_runtime.stop()")
        finally_index = source.index("finally:")
        self.assertGreater(stop_index, finally_index)


if __name__ == "__main__":
    unittest.main()

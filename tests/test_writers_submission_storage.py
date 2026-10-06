from __future__ import annotations

import inspect
import unittest

from writers_submission.storage import PostgresWritersSubmissionStorage


class WritersSubmissionStorageContractTests(unittest.TestCase):
    def test_public_author_methods_require_explicit_actor_identity(self):
        create = inspect.signature(PostgresWritersSubmissionStorage.create_submission)
        listing = inspect.signature(PostgresWritersSubmissionStorage.list_for_author)
        detail = inspect.signature(PostgresWritersSubmissionStorage.get_for_author)
        update = inspect.signature(PostgresWritersSubmissionStorage.update_draft)

        self.assertIn("author_user_id", create.parameters)
        self.assertIn("author_user_id", listing.parameters)
        self.assertIn("author_user_id", detail.parameters)
        self.assertIn("author_user_id", update.parameters)
        self.assertIn("submission_id", detail.parameters)
        self.assertIn("submission_id", update.parameters)
        self.assertIn("expected_version", update.parameters)

    def test_pool_is_not_available_before_initialize(self):
        storage = PostgresWritersSubmissionStorage(
            "postgresql://example.invalid/database"
        )
        self.assertIsNone(storage.pool)
        with self.assertRaisesRegex(RuntimeError, "not initialized"):
            storage._require_pool()


if __name__ == "__main__":
    unittest.main()

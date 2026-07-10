from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import lexicon_approved_storage as storage


class ApprovedLexiconStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        data_dir = Path(self.temp_dir.name)
        self.original_paths = (
            storage.DATA_DIR,
            storage.APPROVED_WORDS_PATH,
            storage.APPROVED_WORDS_BACKUP_PATH,
            storage.APPROVED_WORDS_JOURNAL_PATH,
        )
        storage.DATA_DIR = data_dir
        storage.APPROVED_WORDS_PATH = data_dir / "lexicon_approved_words.txt"
        storage.APPROVED_WORDS_BACKUP_PATH = data_dir / "lexicon_approved_words.backup.txt"
        storage.APPROVED_WORDS_JOURNAL_PATH = data_dir / "lexicon_approved_words.journal.log"

    def tearDown(self) -> None:
        (
            storage.DATA_DIR,
            storage.APPROVED_WORDS_PATH,
            storage.APPROVED_WORDS_BACKUP_PATH,
            storage.APPROVED_WORDS_JOURNAL_PATH,
        ) = self.original_paths
        self.temp_dir.cleanup()

    def test_journal_restores_word_when_snapshot_is_missing(self) -> None:
        storage.save_approved_snapshot({"репа", "дрель"})
        storage.append_approved_word("трель")
        storage.APPROVED_WORDS_PATH.unlink()

        self.assertEqual(storage.load_approved_words(), {"трель"})

    def test_backup_restores_previous_snapshot(self) -> None:
        storage.save_approved_snapshot({"репа", "дрель"})
        storage.save_approved_snapshot({"репа", "дрель", "трель"})
        storage.APPROVED_WORDS_PATH.unlink()

        self.assertEqual(storage.load_approved_words(), {"репа", "дрель"})

    def test_union_keeps_words_from_all_redundant_copies(self) -> None:
        storage.save_approved_snapshot({"репа"})
        storage.append_approved_word("дрель")
        storage.APPROVED_WORDS_BACKUP_PATH.write_text("трель\n", encoding="utf-8")

        self.assertEqual(storage.load_approved_words(), {"репа", "дрель", "трель"})


if __name__ == "__main__":
    unittest.main()

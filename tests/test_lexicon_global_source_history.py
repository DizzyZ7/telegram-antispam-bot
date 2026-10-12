"""Permanent Lexicon source reservations and science pack integration."""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from lexicon_dictionary_patch import (
    DictionaryBackedLexiconService,
    LexiconSourceExhausted,
    LexiconSourceStorageError,
    _source_letter_bag,
)
from lexicon_source_words import LONG_SOURCE_WORDS
from lexicon_source_words_extra3 import LONG_SOURCE_WORDS_EXTRA3
from minigames import MiniGameStorage
from writers_moderation import contains_prohibited_language


class ScienceSourceTests(unittest.TestCase):
    def test_science_bank_adds_at_least_200_genuinely_new_sources(self):
        self.assertGreaterEqual(len(LONG_SOURCE_WORDS_EXTRA3), 200)
        self.assertEqual(len(LONG_SOURCE_WORDS_EXTRA3), len(set(LONG_SOURCE_WORDS_EXTRA3)))
        self.assertEqual(len(LONG_SOURCE_WORDS), len(set(LONG_SOURCE_WORDS)))
        self.assertTrue(set(LONG_SOURCE_WORDS_EXTRA3).issubset(LONG_SOURCE_WORDS))

    def test_science_terms_are_clean_well_formed_words(self):
        for source in LONG_SOURCE_WORDS_EXTRA3:
            with self.subTest(source=source):
                self.assertGreaterEqual(len(source), 10)
                self.assertLessEqual(len(source), 24)
                self.assertTrue(all("а" <= char <= "я" for char in source))
                self.assertFalse(contains_prohibited_language(source))

    def test_chemical_and_geological_examples_are_present(self):
        for source in (
            "хроматография",
            "полимеризация",
            "кристаллохимия",
            "центрифугирование",
            "стратиграфия",
            "петрография",
            "нейрофизиология",
        ):
            self.assertIn(source, LONG_SOURCE_WORDS_EXTRA3)


class GlobalSourceLedgerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "lexicon.db"
        self.storage = MiniGameStorage(self.path)
        await self.storage.initialize()
        self.service = self.make_service(self.storage)

    def make_service(self, storage):
        service = object.__new__(DictionaryBackedLexiconService)
        service.storage = storage
        service.active_word_games = {}
        service.round_candidates = [
            ("радиостанция", {"радио", "рация"}),
            ("хроматография", {"грамм", "графика"}),
            ("полимеризация", {"полимер", "размер"}),
        ]
        return service

    async def asyncTearDown(self):
        await self.storage.close()
        self.temp.cleanup()

    async def test_never_repeat_between_topics_chats_or_after_restart(self):
        word1, _ = await self.service._claim_next_source((-1001, 10))
        word2, _ = await self.service._claim_next_source((-1001, 20))
        self.assertNotEqual(word1, word2)

        # New process / restart, same SQLite data. No in-memory history.
        new_service = self.make_service(self.storage)
        word3, _ = await new_service._claim_next_source((-1002, None))
        self.assertEqual({word1, word2, word3}, {
            "радиостанция", "хроматография", "полимеризация",
        })
        with self.assertRaises(LexiconSourceExhausted):
            await new_service._claim_next_source((-1002, None))

        async with self.storage.connection.execute(
            "SELECT source_word FROM wordgame_source_claims"
        ) as cursor:
            rows = await cursor.fetchall()
        self.assertEqual(len(rows), 3)

        await self.storage.close()
        self.storage = MiniGameStorage(self.path)
        await self.storage.initialize()
        service_again = self.make_service(self.storage)
        with self.assertRaises(LexiconSourceExhausted):
            await service_again._claim_next_source((-1001, 10))

    async def test_concurrent_claims_in_different_topics_never_collide(self):
        claims = await asyncio.gather(
            self.service._claim_next_source((-1001, 10)),
            self.service._claim_next_source((-1002, 20)),
            self.service._claim_next_source((-1003, 30)),
        )
        self.assertEqual(len({word for word, _ in claims}), 3)
        with self.assertRaises(LexiconSourceExhausted):
            await self.service._claim_next_source((-1004, 40))

    async def test_old_cycle_and_archive_words_are_imported_before_selection(self):
        async with self.storage.lock:
            await self.service._ensure_source_cycle_table_locked()
            await self.storage.connection.execute(
                "INSERT INTO wordgame_source_cycle"
                "(chat_id,thread_id,cycle_no,source_word,used_at)"
                "VALUES(?,?,?,?,?)",
                (-1002, 10, 1, "радиостанция", 12345),
            )
            await self.storage.connection.execute(
                "INSERT INTO wordgame_round_archive"
                "(chat_id,day_key,week_key,round_code,base_word,"
                "found_count,total_words,winner_name,winner_points,created_at)"
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (-1005, "2026-01-01", "2026-W01", "X123",
                 "хроматография", 2, 70, "Тест", 5, 12346),
            )
            await self.storage.connection.commit()

        word, _ = await self.service._claim_next_source((-1001, 77))
        self.assertEqual(word, "полимеризация")
        with self.assertRaises(LexiconSourceExhausted):
            await self.service._claim_next_source((-1001, 77))

    async def test_existing_anagram_of_old_source_is_also_blocked(self):
        self.service.round_candidates = [
            ("абвгдежзик", {"абвг"}),
            ("зжедгвбаик", {"абвг"}),  # exactly same letters
            ("хроматография", {"граф"}),
        ]
        assert _source_letter_bag("абвгдежзик") == _source_letter_bag("зжедгвбаик")
        async with self.storage.lock:
            await self.service._ensure_source_cycle_table_locked()
            await self.storage.connection.execute(
                "INSERT INTO wordgame_source_cycle"
                "(chat_id,thread_id,cycle_no,source_word,used_at)"
                "VALUES(?,?,?,?,?)",
                (-1001, 10, 1, "абвгдежзик", 123),
            )
            await self.storage.connection.commit()
        word, _ = await self.service._claim_next_source((-1001, 11))
        self.assertEqual(word, "хроматография")
        with self.assertRaises(LexiconSourceExhausted):
            await self.service._claim_next_source((-1001, 11))

    async def test_empty_bank_or_unavailable_storage_cannot_force_repeats(self):
        self.service.round_candidates = []
        with self.assertRaises(LexiconSourceExhausted):
            await self.service._claim_next_source((-1001, 10))
        self.service.round_candidates = [("радиостанция", {"радио"})]
        original = self.service.storage.connection
        self.service.storage.connection = None
        try:
            with self.assertRaises(LexiconSourceStorageError):
                await self.service._claim_next_source((-1001, 10))
        finally:
            self.service.storage.connection = original


if __name__ == "__main__":
    unittest.main()

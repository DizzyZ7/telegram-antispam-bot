"""Reusable Lexicon cycles, persisted migration and science word bank."""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from lexicon_balanced_storage import save_balanced_round
from lexicon_dictionary_patch import (
    DictionaryBackedLexiconService,
    LexiconSourceExhausted,
    LexiconSourceStorageError,
    _source_letter_bag,
)
from lexicon_source_words import LONG_SOURCE_WORDS
from lexicon_source_words_extra3 import LONG_SOURCE_WORDS_EXTRA3
from minigames import MiniGameStorage, PlayerResult, WordGameRound
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

    async def test_full_cycle_restarts_without_immediate_boundary_repeat(self):
        first_cycle = [
            (await self.service._claim_next_source((-1001, number)))[0]
            for number in (1, 2, 3)
        ]
        self.assertEqual(len(set(first_cycle)), 3)
        self.assertEqual(await self.service.source_cycle_status(), (1, 0))

        first_second_cycle, _ = await self.service._claim_next_source((-2000, 1))
        self.assertIn(first_second_cycle, first_cycle)
        self.assertNotEqual(first_second_cycle, first_cycle[-1])
        self.assertEqual(await self.service.source_cycle_status(), (2, 2))
        self.assertEqual(await self.service.count_unplayed_sources(), 2)

        second_cycle = [first_second_cycle] + [
            (await self.service._claim_next_source((-1001, number)))[0]
            for number in (4, 5)
        ]
        self.assertEqual(len(set(second_cycle)), 3)
        self.assertEqual(set(second_cycle), set(first_cycle))
        self.assertEqual(await self.service.source_cycle_status(), (2, 0))

        next_word, _ = await self.service._claim_next_source((-2000, 2))
        self.assertNotEqual(next_word, second_cycle[-1])
        self.assertEqual(await self.service.source_cycle_status(), (3, 2))

        async with self.storage.connection.execute(
            "SELECT cycle_no, source_word FROM wordgame_source_cycle_history"
        ) as cursor:
            archive = await cursor.fetchall()
        self.assertEqual(len(archive), 6)
        self.assertEqual({int(row[0]) for row in archive}, {1, 2})

    async def test_cycle_state_persists_across_full_sqlite_restart(self):
        first = (await self.service._claim_next_source((-1001, 1)))[0]
        second = (await self.service._claim_next_source((-1001, 2)))[0]
        await self.storage.close()
        self.storage = MiniGameStorage(self.path)
        await self.storage.initialize()
        restarted_service = self.make_service(self.storage)
        self.assertEqual(await restarted_service.source_cycle_status(), (1, 1))
        third = (await restarted_service._claim_next_source((-2000, 4)))[0]
        self.assertEqual(len({first, second, third}), 3)
        fourth = (await restarted_service._claim_next_source((-2000, 5)))[0]
        self.assertNotEqual(third, fourth)
        self.assertEqual(await restarted_service.source_cycle_status(), (2, 2))
        await self.storage.close()
        self.storage = MiniGameStorage(self.path)
        await self.storage.initialize()
        second_restart = self.make_service(self.storage)
        self.assertEqual(await second_restart.source_cycle_status(), (2, 2))

    async def test_concurrent_claims_in_different_topics_never_collide(self):
        claims = await asyncio.gather(
            self.service._claim_next_source((-1001, 10)),
            self.service._claim_next_source((-1002, 20)),
            self.service._claim_next_source((-1003, 30)),
        )
        self.assertEqual(len({word for word, _ in claims}), 3)
        next_word, _ = await self.service._claim_next_source((-1004, 40))
        self.assertIn(next_word, {word for word, _ in claims})
        self.assertNotEqual(next_word, claims[-1][0])
        self.assertEqual(await self.service.source_cycle_status(), (2, 2))

    async def test_active_game_excluded_even_at_cycle_boundary(self):
        active = WordGameRound(
            chat_id=-1001,
            round_code="ACTV",
            base_word="радиостанция",
            allowed_words={"радио"},
            min_length=4,
            started_at=1.0,
            ends_at=999.0,
            message_thread_id=10,
        )
        self.service.active_word_games[(-1001, 10)] = active
        first, _ = await self.service._claim_next_source((-1002, 20))
        second, _ = await self.service._claim_next_source((-1002, 30))
        self.assertEqual({first, second}, {"хроматография", "полимеризация"})
        repeated, _ = await self.service._claim_next_source((-1002, 40))
        self.assertNotEqual(repeated, active.base_word)
        self.assertNotEqual(repeated, second)
        self.assertEqual(await self.service.source_cycle_status(), (2, 1))

    async def test_only_active_word_waits_for_finished_game(self):
        self.service.round_candidates = [("радиостанция", {"радио"})]
        self.service.active_word_games[(-1001, 10)] = WordGameRound(
            chat_id=-1001, round_code="A1", base_word="радиостанция",
            allowed_words={"радио"}, min_length=4,
            started_at=1.0, ends_at=9999.0, message_thread_id=10,
        )
        with self.assertRaises(LexiconSourceExhausted):
            await self.service._claim_next_source((-1002, 10))
        self.service.active_word_games.clear()
        first, _ = await self.service._claim_next_source((-1002, 10))
        again, _ = await self.service._claim_next_source((-1002, 10))
        self.assertEqual(first, again)  # Single-source cycle necessarily repeats.
        self.assertEqual(await self.service.source_cycle_status(), (2, 0))

    async def test_legacy_cycle_and_archive_import_only_once(self):
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

        final_word, _ = await self.service._claim_next_source((-1001, 77))
        self.assertEqual(final_word, "полимеризация")
        repeat, _ = await self.service._claim_next_source((-1001, 78))
        self.assertNotEqual(final_word, repeat)
        self.assertEqual(await self.service.source_cycle_status(), (2, 2))

        # Reopening must NOT import legacy rows into the restarted cycle.
        await self.storage.close()
        self.storage = MiniGameStorage(self.path)
        await self.storage.initialize()
        again = self.make_service(self.storage)
        self.assertEqual(await again.source_cycle_status(), (2, 2))
        second, _ = await again._claim_next_source((-1001, 79))
        third, _ = await again._claim_next_source((-1001, 80))
        self.assertEqual(len({repeat, second, third}), 3)

    async def test_v3_claims_migrate_without_erasing_existing_progress(self):
        await self.storage.connection.execute(
            "CREATE TABLE wordgame_source_claims("
            "source_word TEXT PRIMARY KEY, letter_bag TEXT NOT NULL UNIQUE,"
            "chat_id INTEGER NOT NULL, thread_id INTEGER NOT NULL,"
            "used_at INTEGER NOT NULL)"
        )
        await self.storage.connection.execute(
            "INSERT INTO wordgame_source_claims VALUES(?,?,?,?,?)",
            ("радиостанция", _source_letter_bag("радиостанция"), -1, 0, 100),
        )
        await self.storage.connection.commit()
        self.assertEqual(await self.service.source_cycle_status(), (1, 2))
        used_word, _ = await self.service._claim_next_source((-1, 2))
        self.assertNotEqual(used_word, "радиостанция")

    async def test_historic_anagram_does_not_double_count_cycle(self):
        self.service.round_candidates = [
            ("абвгдежзик", {"абвг"}),
            ("зжедгвбаик", {"абвг"}),  # same bag, not a distinct puzzle
            ("хроматография", {"граф"}),
        ]
        self.assertEqual(
            _source_letter_bag("абвгдежзик"), _source_letter_bag("зжедгвбаик"),
        )
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
        fresh, _ = await self.service._claim_next_source((-1001, 12))
        self.assertIn(fresh, {"абвгдежзик", "зжедгвбаик"})
        self.assertEqual(await self.service.source_cycle_status(), (2, 1))

    async def test_empty_bank_or_unavailable_storage_does_not_bypass_safety(self):
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


    async def test_failed_score_update_rolls_back_and_game_can_restart(self):
        # Regression: a failed round save left an open SQLite transaction,
        # causing every later /minigame to fail with a nested BEGIN.
        original_upsert = self.storage._upsert_score
        calls = 0

        async def interrupted_upsert(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated interrupted score storage")
            return await original_upsert(**kwargs)

        self.storage._upsert_score = interrupted_upsert
        try:
            with self.assertRaises(OSError):
                await save_balanced_round(
                    self.storage,
                    chat_id=-1001,
                    players=[PlayerResult(user_id=11, name="Alice", points=9)],
                    day_key="2026-10-15",
                    week_key="2026-W42",
                    round_code="FAIL",
                    base_word="радиостанция",
                    found_count=1,
                    total_words=28,
                )
        finally:
            self.storage._upsert_score = original_upsert

        self.assertFalse(self.storage.connection.in_transaction)
        async with self.storage.connection.execute(
            "SELECT COUNT(*) FROM wordgame_scores"
        ) as cursor:
            self.assertEqual((await cursor.fetchone())[0], 0)
        source, _ = await self.service._claim_next_source((-1001, 10))
        self.assertIn(source, {"радиостанция", "хроматография", "полимеризация"})

    async def test_claim_recovers_stale_legacy_transaction_without_erasing_history(self):
        first, _ = await self.service._claim_next_source((-1001, 10))
        # Emulate old-version partial write on the shared connection.
        await self.storage.connection.execute(
            "INSERT INTO wordgame_scores "
            "(chat_id,user_id,name,rounds,wins,total_points,best_points,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (-1001, 876, "stale", 1, 0, 5, 5, 100),
        )
        self.assertTrue(self.storage.connection.in_transaction)
        second, _ = await self.service._claim_next_source((-1001, 11))
        self.assertNotEqual(first, second)
        self.assertFalse(self.storage.connection.in_transaction)
        async with self.storage.connection.execute(
            "SELECT COUNT(*) FROM wordgame_scores WHERE user_id = ?", (876,)
        ) as cursor:
            self.assertEqual((await cursor.fetchone())[0], 0)
        async with self.storage.connection.execute(
            "SELECT COUNT(*) FROM wordgame_source_claims"
        ) as cursor:
            self.assertEqual((await cursor.fetchone())[0], 2)



if __name__ == "__main__":
    unittest.main()

"""Durable private games against disposable, isolated real PostgreSQL schemas."""
from __future__ import annotations

import asyncio
import os
import unittest
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import uuid

from fosgen_pets.core import build_appearance
from fosgen_pets.postgres import PostgresPetStore

URL = os.getenv('TEST_DATABASE_URL', '').strip()


@unittest.skipUnless(URL, 'TEST_DATABASE_URL is required (real PostgreSQL only)')
class DraughtsPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import asyncpg
        from fosgen_pets.draughts_store import DraughtsStore
        self.schema = 'draughts_test_' + uuid.uuid4().hex
        self.admin = await asyncpg.connect(URL)
        await self.admin.execute(f'CREATE SCHEMA "{self.schema}"')
        parts = urlsplit(URL)
        query = dict(parse_qsl(parts.query), search_path=self.schema)
        self.dsn = urlunsplit(parts._replace(query=urlencode(query)))
        self.pets = PostgresPetStore(self.dsn)
        await self.pets.initialize()
        self.games = DraughtsStore(self.pets)

    async def asyncTearDown(self):
        await self.pets.close()
        await self.admin.execute(f'DROP SCHEMA "{self.schema}" CASCADE')
        await self.admin.close()

    async def adopt(self, owner=11):
        await self.pets.opt_in(owner)
        async with self.pets._pool.acquire() as conn:
            await conn.execute('UPDATE fosgen_pet_opt_ins SET qualifying_count=100 WHERE user_id=$1', owner)
        return await self.pets.adopt(owner, appearance_factory=lambda attempt: build_appearance('kitten', f'game-{owner}', attempt))

    def state(self, game, **changes):
        result = dict(board=game.board, side=game.side, selected=game.selected,
                      status=game.status, no_progress=game.no_progress, repetitions=game.repetitions)
        result.update(changes)
        return result

    async def test_requires_permanent_pet_and_concurrent_start_is_one_game(self):
        self.assertIsNone(await self.games.start(11))
        pet = await self.adopt()
        games = await asyncio.gather(*(self.games.start(11) for _ in range(6)))
        self.assertEqual(len({game.id for game in games}), 1)
        self.assertEqual(games[0].pet_id, pet.id)
        self.assertEqual(games[0].board.count(1), 12)
        self.assertEqual(games[0].board.count(-1), 12)
        self.assertIsNone(await self.games.resume(12))

    async def test_restart_restores_selection_ai_turn_and_message_binding(self):
        await self.adopt()
        game = await self.games.start(11)
        saved = await self.games.save(11, game.id, game.revision,
            **self.state(game, side=-1, selected=(40, 33), no_progress=7, repetitions={'position': 2}),
            chat_id=11, message_id=155)
        await self.pets.close()
        self.pets = PostgresPetStore(self.dsn)
        from fosgen_pets.draughts_store import DraughtsStore
        self.games = DraughtsStore(self.pets)
        self.assertEqual(await self.games.resume(11), saved)

    async def test_cas_owner_and_restarted_game_reject_stale_actions(self):
        pet = await self.adopt()
        game = await self.games.start(11)
        results = await asyncio.gather(*(self.games.save(11, game.id, 0,
            **self.state(game, selected=(40,))) for _ in range(6)))
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertIsNone(await self.games.save(12, game.id, 1, **self.state(game)))
        replacement = await self.games.start(11, replace=True)
        self.assertNotEqual(replacement.id, game.id)
        self.assertIsNone(await self.games.save(11, game.id, 1, **self.state(game)))
        self.assertEqual(await self.pets.get(11), pet)

    async def test_invalid_or_unbounded_state_is_rejected_without_update(self):
        await self.adopt()
        game = await self.games.start(11)
        invalid = [dict(board=(0,)), dict(board=(3,) * 64), dict(side=0),
                   dict(selected=(64,)), dict(status='oops'), dict(no_progress=-1),
                   dict(repetitions={'x' * 1000: 1}), dict(repetitions={'x': 0})]
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                await self.games.save(11, game.id, 0, **self.state(game, **changes))
        self.assertEqual(await self.games.resume(11), game)

    async def test_terminal_result_survives_restart_and_preserves_binding(self):
        await self.adopt()
        game = await self.games.start(11)
        bound = await self.games.save(11, game.id, 0, **self.state(game), chat_id=11, message_id=31)
        terminal = await self.games.save(11, game.id, 1, **self.state(bound, status='draw'))
        self.assertEqual((terminal.chat_id, terminal.message_id), (11, 31))
        self.assertEqual((await self.games.start(11)).status, 'draw')

    async def test_database_rejects_pet_rebinding_and_revision_rollback(self):
        import asyncpg
        await self.adopt()
        other_pet = await self.adopt(12)
        game = await self.games.start(11)
        await self.games.save(11, game.id, 0, **self.state(game))
        async with self.pets._pool.acquire() as conn:
            for sql, args in (
                ('UPDATE fosgen_pet_draughts_games SET pet_id=$1 WHERE owner_user_id=11', (other_pet.id,)),
                ('UPDATE fosgen_pet_draughts_games SET revision=0 WHERE owner_user_id=11', ()),
            ):
                with self.subTest(sql=sql), self.assertRaises(asyncpg.CheckViolationError):
                    await conn.execute(sql, *args)

    async def test_replacement_cas_cannot_erase_a_newer_turn_or_game(self):
        await self.adopt()
        game = await self.games.start(11)
        await self.games.save(11, game.id, 0, **self.state(game, selected=(40,)))
        self.assertIsNone(await self.games.start(11, replace=True,
            expected_game_id=game.id, expected_revision=0))
        replacement = await self.games.start(11, replace=True,
            expected_game_id=game.id, expected_revision=1)
        self.assertIsNotNone(replacement)
        self.assertIsNone(await self.games.start(11, replace=True,
            expected_game_id=game.id, expected_revision=1))
        self.assertEqual(await self.games.resume(11), replacement)

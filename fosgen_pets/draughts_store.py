"""Durable private draughts state; owner-scoped revision CAS arbitrates taps.

The rules/UI supply complete transitions. The database never touches permanent
pet identity or assets, and stores enough state to finish an interrupted AI turn.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import secrets
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .postgres import PostgresPetStore


@dataclass(frozen=True)
class Game:
    id: str
    owner_id: int
    pet_id: int
    board: tuple[int, ...]
    side: int
    revision: int
    selected: tuple[int, ...]
    status: str
    no_progress: int
    repetitions: dict[str, int]
    chat_id: int | None = None
    message_id: int | None = None


def _initial_board() -> tuple[int, ...]:
    return tuple((-1 if row < 3 else 1 if row > 4 else 0)
                 if (row + col) % 2 else 0 for row in range(8) for col in range(8))


def _game(row) -> Game | None:
    if row is None:
        return None
    repetitions = row['repetitions']
    if isinstance(repetitions, str):
        repetitions = json.loads(repetitions)
    return Game(row['game_id'], row['owner_user_id'], row['pet_id'], tuple(row['board']),
                row['side'], row['revision'], tuple(row['selected']), row['status'],
                row['no_progress'], repetitions, row['chat_id'], row['message_id'])


def _validate(board, side, selected, status, no_progress, repetitions):
    if len(board) != 64 or any(type(piece) is not int or piece not in (-2, -1, 0, 1, 2) for piece in board):
        raise ValueError('Draughts board must contain exactly 64 valid pieces')
    if type(side) is not int or side not in (-1, 1):
        raise ValueError('Invalid draughts side')
    if len(selected) > 32 or any(type(square) is not int or not 0 <= square < 64 for square in selected):
        raise ValueError('Invalid selected move path')
    if status not in ('active', 'white_won', 'black_won', 'draw'):
        raise ValueError('Invalid game status')
    if type(no_progress) is not int or not 0 <= no_progress <= 10000:
        raise ValueError('Invalid no-progress counter')
    if not isinstance(repetitions, dict) or len(repetitions) > 2048:
        raise ValueError('Invalid repetition history')
    if any(not isinstance(key, str) or not 1 <= len(key) <= 160
           or type(count) is not int or not 1 <= count <= 10000
           for key, count in repetitions.items()):
        raise ValueError('Invalid repetition history entry')
    encoded = json.dumps(repetitions, ensure_ascii=True, separators=(',', ':'))
    if len(encoded) > 131072:
        raise ValueError('Repetition history is too large')
    return encoded


class DraughtsStore:
    """Share the permanent-pet pool and its advisory-locked additive migration."""

    def __init__(self, pet_store: PostgresPetStore):
        self.pet_store = pet_store

    async def resume(self, owner_id: int) -> Game | None:
        await self.pet_store.initialize()
        async with self.pet_store._pool.acquire(timeout=15) as conn:
            return _game(await conn.fetchrow(
                'SELECT * FROM fosgen_pet_draughts_games WHERE owner_user_id=$1', owner_id))

    async def start(self, owner_id: int, replace: bool = False, *,
                    expected_game_id: str | None = None,
                    expected_revision: int | None = None) -> Game | None:
        if (expected_game_id is None) != (expected_revision is None):
            raise ValueError('Replacement CAS requires both game id and revision')
        if expected_game_id is not None and not replace:
            raise ValueError('Replacement CAS requires replace=True')
        await self.pet_store.initialize()
        async with self.pet_store._pool.acquire(timeout=15) as conn:
            async with conn.transaction():
                # Existing permanent pet row serializes concurrent first starts too.
                pet_id = await conn.fetchval(
                    'SELECT pet_id FROM fosgen_pets WHERE owner_user_id=$1 FOR UPDATE', owner_id)
                if pet_id is None:
                    return None
                existing = await conn.fetchrow(
                    'SELECT * FROM fosgen_pet_draughts_games WHERE owner_user_id=$1 FOR UPDATE', owner_id)
                if expected_game_id is not None and (existing is None
                        or existing['game_id'] != expected_game_id
                        or existing['revision'] != expected_revision):
                    return None
                if not replace and existing is not None:
                    return _game(existing)
                return _game(await conn.fetchrow('''INSERT INTO fosgen_pet_draughts_games
                    (owner_user_id, pet_id, game_id, board) VALUES($1,$2,$3,$4)
                    ON CONFLICT(owner_user_id) DO UPDATE SET
                        game_id=EXCLUDED.game_id, board=EXCLUDED.board, side=1,
                        revision=0, selected='{}', status='active', no_progress=0,
                        repetitions='{}', chat_id=NULL, message_id=NULL, updated_at=NOW()
                    RETURNING *''', owner_id, pet_id, secrets.token_hex(6), list(_initial_board())))

    async def save(self, owner_id: int, game_id: str, expected_revision: int, *,
                   board, side: int, selected=(), status: str = 'active',
                   no_progress: int = 0, repetitions=None,
                   chat_id: int | None = None, message_id: int | None = None) -> Game | None:
        repetitions = {} if repetitions is None else repetitions
        encoded = _validate(board, side, selected, status, no_progress, repetitions)
        if (chat_id is None) != (message_id is None):
            raise ValueError('Message binding requires both chat and message identifiers')
        if chat_id is not None and (type(chat_id) is not int or type(message_id) is not int
                                    or chat_id != owner_id or message_id <= 0):
            raise ValueError('Games may only bind to an owner private message')
        await self.pet_store.initialize()
        async with self.pet_store._pool.acquire(timeout=15) as conn:
            return _game(await conn.fetchrow('''UPDATE fosgen_pet_draughts_games SET
                board=$4, side=$5, selected=$6, status=$7, no_progress=$8,
                repetitions=$9::jsonb, chat_id=COALESCE($10,chat_id),
                message_id=COALESCE($11,message_id), revision=revision+1, updated_at=NOW()
                WHERE owner_user_id=$1 AND game_id=$2 AND revision=$3 RETURNING *''',
                owner_id, game_id, expected_revision, list(board), side, list(selected),
                status, no_progress, encoded, chat_id, message_id))

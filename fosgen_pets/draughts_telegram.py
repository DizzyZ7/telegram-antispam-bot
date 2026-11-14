"""Private, revision-bound Russian draughts with persisted full capture paths."""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from aiogram import F
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from . import draughts

logger = logging.getLogger(__name__)
_AI_SLOTS = asyncio.Semaphore(2)
_PAYLOAD = re.compile(r"dc:([a-zA-Z0-9-]{1,36}):([0-9]{1,12}):(new|yes|back|clear|noop|[0-9]{1,2})\Z")
_OPEN = re.compile(r"petdraughts:([1-9][0-9]{0,18}):open\Z")
_UNAVAILABLE = "Шашки временно недоступны. Партия сохраняется. Открой /pet_checkers позже."


def _owner(message: Message, user: Any = None) -> int:
    user = user or message.from_user
    if (message.chat.type != 'private' or user is None or user.is_bot
            or user.id != message.chat.id or message.sender_chat is not None):
        raise SkipHandler
    return user.id


def _key(board, side):
    return ','.join(map(str, board)) + ':' + str(side)


def _next(game):
    if game.status != 'active' or game.side != 1:
        return set()
    moves = draughts.legal_moves(game.board, 1)
    prefix = game.selected
    return {m.path[len(prefix)] for m in moves
            if len(m.path) > len(prefix) and m.path[:len(prefix)] == prefix}


def _keyboard(game, *, confirm=False):
    def button(text, action):
        return InlineKeyboardButton(text=text, callback_data=f'dc:{game.id}:{game.revision}:{action}')
    if confirm:
        return InlineKeyboardMarkup(inline_keyboard=[[
            button('Да, новая партия', 'yes'), button('Продолжить текущую', 'back')]])
    targets = _next(game)
    icons = {1: '⚪', -1: '⚫', 2: '♔', -2: '♚', 0: '·'}
    rows = []
    for row in range(8):
        cells = []
        for col in range(8):
            square = row * 8 + col
            text = icons[game.board[square]] if (row+col)%2 else '▫'
            if square in game.selected:
                text = '◉' if square == game.selected[-1] else '•'
            elif square in targets:
                text = '🔹' + text
            cells.append(button(text, str(square)))
        rows.append(cells)
    rows.append([button('Снять выбор', 'clear'), button('Новая партия', 'new')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _text(game):
    status = {'white_won': 'Ты победил!', 'black_won': 'Питомец победил.',
              'draw': 'Ничья.', 'active': 'Твой ход: выбери шашку, затем клетку с 🔹.'}.get(game.status, game.status)
    if game.status == 'active' and game.side == -1:
        status = 'Питомец думает. Если ход прервался, открой /pet_checkers.'
    elif game.status == 'active' and len(game.selected) > 1:
        status = 'Продолжи взятие: выбери следующую клетку с 🔹.'
    return ('Шашки с питомцем\nТы: ⚪ / ♔. Питомец: ⚫ / ♚.\n'
            + status + '\nВзятие обязательно, серия завершается целиком. '
            'Партия не меняет внешность и показатели питомца.')


class DraughtsTelegramService:
    def __init__(self, app, pet_store, game_store):
        self.app, self.pets, self.games = app, pet_store, game_store
        self._registered = []

    async def _save(self, game, **updates):
        fields = {key: getattr(game, key) for key in
                  ('board', 'side', 'selected', 'status', 'no_progress', 'repetitions', 'chat_id', 'message_id')}
        fields.update(updates)
        return await self.games.save(game.owner_id, game.id, game.revision, **fields)

    async def _edit(self, message, game, *, confirm=False):
        # Telegram edits have no CAS. Reconcile both sides of each network await,
        # including replacement games, so a late response cannot strand the UI.
        for _ in range(4):
            current = await self.games.resume(game.owner_id)
            if current is None or (current.chat_id, current.message_id) != (
                    message.chat.id, message.message_id):
                return
            if (current.id, current.revision) != (game.id, game.revision):
                confirm = False
            game = current
            try:
                await message.edit_text(
                    'Начать новую партию? Текущая партия будет заменена.' if confirm else _text(game),
                    reply_markup=_keyboard(game, confirm=confirm), parse_mode=None)
            except TelegramBadRequest as exc:
                if 'message is not modified' not in str(exc).lower():
                    raise
            current = await self.games.resume(game.owner_id)
            if current is None or (current.chat_id, current.message_id) != (
                    message.chat.id, message.message_id):
                return
            if (current.id, current.revision) == (game.id, game.revision):
                return
            game, confirm = current, False
        # Continuous updates across workers must still leave an actionable escape,
        # rather than a board whose buttons reference an obsolete revision.
        await message.edit_text('Партия обновилась. Открой /pet_checkers, чтобы продолжить.',
                                reply_markup=None, parse_mode=None)

    async def _move(self, game, move):
        board = draughts.apply_move(game.board, move)
        side = -game.side
        quiet = game.no_progress + 1 if not move.captured and abs(game.board[move.path[0]]) == 2 else 0
        repeats = dict(game.repetitions or {}) if quiet else {}
        # Irreversible moves cannot repeat old positions: bound history per quiet run.
        if quiet:
            repeats.setdefault(_key(game.board, game.side), 1)
        key = _key(board, side)
        repeats[key] = repeats.get(key, 0) + 1
        status = 'active'
        if not draughts.legal_moves(board, side):
            status = 'white_won' if game.side == 1 else 'black_won'
        elif quiet >= 30 or repeats[key] >= 3:
            status = 'draw'
        return await self._save(game, board=board, side=side, selected=(), status=status,
                                no_progress=quiet, repetitions=repeats)

    async def _pet_turn(self, game):
        if game.status != 'active' or game.side != -1:
            return game
        # No connection or transaction spans the CPU search. A stale result loses CAS.
        async with _AI_SLOTS:
            current = await self.games.resume(game.owner_id)
            if current is None or (current.id,current.revision) != (game.id,game.revision):
                return None
            search = asyncio.create_task(asyncio.to_thread(
                draughts.choose_move, game.board, -1, time_limit=0.7, max_depth=12))
            try:
                move = await asyncio.shield(search)
            except asyncio.CancelledError:
                # Keep the slot until the worker stops, including during shutdown.
                await search
                raise
        if move is None:
            return await self._save(game, status='white_won', selected=())
        if move not in draughts.legal_moves(game.board, -1):
            raise ValueError('Invalid engine move')
        return await self._move(game, move)

    async def _open(self, message, owner):
        game = await self.games.start(owner)
        if game is None:
            await message.answer('Сначала нужен питомец. Открой /pet.', parse_mode=None)
            return
        # A new message gets a new binding; buttons in all previous messages expire.
        board_message = await message.answer('Открываю сохраненную партию…', parse_mode=None)
        board_message = board_message.as_(message.bot)
        game = await self._save(game, chat_id=board_message.chat.id, message_id=board_message.message_id)
        if game is None:
            await board_message.edit_text('Партия уже обновилась. Открой /pet_checkers.', parse_mode=None)
            return
        await self._edit(board_message, game)
        result = await self._pet_turn(game)
        if result is not None and result.revision != game.revision:
            await self._edit(board_message, result)

    async def command(self, message: Message):
        owner = _owner(message)
        try:
            await self._open(message, owner)
        except Exception as exc:
            logger.warning('FOSGEN_DRAUGHTS_COMMAND_FAILED reason=%s', type(exc).__name__)
            await message.answer(_UNAVAILABLE, parse_mode=None)

    async def callback(self, callback: CallbackQuery):
        if not isinstance(callback.message, Message):
            raise SkipHandler
        message = callback.message
        owner = _owner(message, callback.from_user)
        data = callback.data or ''
        opening = _OPEN.fullmatch(data)
        match = _PAYLOAD.fullmatch(data)
        if not opening and not match:
            await callback.answer('Кнопка устарела. Открой /pet_checkers.')
            return
        await callback.answer()
        try:
            if opening:
                pet = await self.pets.get(owner)
                if pet is not None and pet.id == int(opening[1]):
                    await self._open(message, owner)
                return
            game = await self.games.resume(owner)
            game_id, revision, action = match.groups()
            if game is None or (game.id,game.revision,game.chat_id,game.message_id) != (
                    game_id,int(revision),message.chat.id,message.message_id):
                return
            if action == 'new':
                await self._edit(message, game, confirm=True)
                return
            if action == 'yes':
                game = await self.games.start(
                    owner, replace=True, expected_game_id=game.id, expected_revision=game.revision)
                if game is not None:
                    game = await self._save(game, chat_id=message.chat.id, message_id=message.message_id)
            elif action == 'back':
                await self._edit(message, game)
                return
            elif action == 'clear':
                game = await self._save(game, selected=())
            elif action.isdecimal() and game.side == 1 and game.status == 'active':
                square = int(action)
                if square not in _next(game):
                    return
                prefix = game.selected + (square,)
                moves = [m for m in draughts.legal_moves(game.board,1) if m.path[:len(prefix)] == prefix]
                complete = next((m for m in moves if m.path == prefix),None)
                game = await self._move(game,complete) if complete else await self._save(game,selected=prefix)
            else:
                return
            if game is not None:
                await self._edit(message,game)
                result = await self._pet_turn(game)
                if result is not None and result.revision != game.revision:
                    await self._edit(message,result)
        except Exception as exc:
            logger.warning('FOSGEN_DRAUGHTS_CALLBACK_FAILED reason=%s',type(exc).__name__)
            await message.answer(_UNAVAILABLE,parse_mode=None)


def register_draughts_handlers(app, store, *, game_store=None):
    if game_store is None:
        from .draughts_store import DraughtsStore
        game_store = DraughtsStore(store)
    service = DraughtsTelegramService(app,store,game_store)
    command, callback = service.command, service.callback
    app.dp.message.register(command,Command('pet_checkers'))
    app.dp.callback_query.register(callback,F.data.startswith('dc:') | F.data.startswith('petdraughts:'))
    service._registered = [(app.dp.message,command),(app.dp.callback_query,callback)]
    return service

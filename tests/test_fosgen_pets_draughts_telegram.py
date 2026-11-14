"""Real dispatcher coverage; only persistence and Telegram transport are replaced."""
from dataclasses import dataclass, replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from aiogram import Bot, Dispatcher
from aiogram.methods import AnswerCallbackQuery, EditMessageText, GetMe, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from tests.test_fosgen_pets_telegram import RecordingSession, NOW, OWNER, message

@dataclass(frozen=True)
class Game:
    id: str = 'abc123'
    owner_id: int = OWNER
    pet_id: int = 9
    board: tuple = ()
    side: int = 1
    revision: int = 0
    selected: tuple = ()
    status: str = 'active'
    no_progress: int = 0
    repetitions: object = None
    chat_id: int = None
    message_id: int = None

class Store:
    def __init__(self):
        self.game = None
        self.writes = 0
    async def resume(self, owner):
        return self.game if owner == OWNER else None
    async def start(self, owner, replace=False, expected_game_id=None, expected_revision=None):
        from fosgen_pets.draughts import initial_board
        if owner != OWNER:
            return None
        if expected_game_id is not None and (self.game.id,self.game.revision) != (expected_game_id,expected_revision):
            return None
        if self.game is None or replace:
            self.game = Game(board=initial_board(), repetitions={})
            self.writes += 1
        return self.game
    async def save(self, owner, game_id, expected_revision, **fields):
        g = self.game
        if not g or (owner, game_id, expected_revision) != (g.owner_id,g.id,g.revision):
            return None
        fields = {k:v for k,v in fields.items() if v is not None}
        self.game = replace(g, revision=g.revision+1, **fields)
        self.writes += 1
        return self.game

class Session(RecordingSession):
    async def make_request(self, bot, method, timeout=None):
        if isinstance(method, EditMessageText):
            self.requests.append(method)
            return Message(message_id=method.message_id,date=NOW,chat=Chat(id=OWNER,type='private'))
        return await super().make_request(bot,method,timeout)

class DraughtsTelegramTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from fosgen_pets.draughts_telegram import register_draughts_handlers
        self.session=Session()
        self.bot=Bot('777:'+'a'*35,session=self.session)
        self.addAsyncCleanup(self.bot.session.close)
        self.app=SimpleNamespace(dp=Dispatcher())
        self.games=Store()
        async def get(owner):
            return SimpleNamespace(id=9) if owner == OWNER else None
        self.service=register_draughts_handlers(self.app,SimpleNamespace(get=get),game_store=self.games)
        self.n=0
    async def dispatch(self,event):
        self.n+=1
        return await self.app.dp.feed_update(self.bot,Update(update_id=self.n,**{'callback_query' if isinstance(event,CallbackQuery) else 'message':event}))
    def cb(self,action,revision=None,owner=OWNER,msgid=901,chat=OWNER):
        g=self.games.game
        return CallbackQuery(id=str(self.n),from_user=User(id=owner,is_bot=False,first_name='U'),chat_instance='x',data=f'dc:{g.id}:{g.revision if revision is None else revision}:{action}',message=message(None,owner=777,chat=chat).model_copy(update={'message_id':msgid}))
    async def test_open_binds_persisted_board_and_64_buttons(self):
        await self.dispatch(message('/pet_checkers',chat=OWNER))
        g=self.games.game
        self.assertEqual((g.chat_id,g.message_id),(OWNER,901))
        edits=[x for x in self.session.requests if isinstance(x,EditMessageText)]
        board=edits[-1].reply_markup.inline_keyboard[:8]
        self.assertEqual([len(row) for row in board],[8]*8)
        self.assertTrue(all(len(b.callback_data.encode())<=64 for row in board for b in row))
    async def test_stale_foreign_message_and_group_taps_do_not_mutate(self):
        await self.dispatch(message('/pet_checkers',chat=OWNER))
        before=self.games.writes
        for cb in (self.cb('40',revision=0),self.cb('40',owner=12),self.cb('40',msgid=902),self.cb('40',chat=-11)):
            await self.dispatch(cb)
        self.assertEqual(self.games.writes,before)
    async def test_source_selection_is_persisted_and_replay_rejected(self):
        await self.dispatch(message('/pet_checkers',chat=OWNER))
        cb=self.cb('40')
        await self.dispatch(cb)
        self.assertEqual(self.games.game.selected,(40,))
        writes=self.games.writes
        await self.dispatch(cb)
        self.assertEqual(self.games.writes,writes)
    async def test_reset_requires_confirmation(self):
        await self.dispatch(message('/pet_checkers',chat=OWNER))
        writes=self.games.writes
        await self.dispatch(self.cb('new'))
        self.assertEqual(self.games.writes,writes)
        self.assertIn('Начать новую',self.session.requests[-1].text)
    async def test_group_command_never_creates_game(self):
        await self.dispatch(message('/pet_checkers',chat=-11))
        self.assertIsNone(self.games.game)

    async def test_capture_prefix_survives_reopen_without_partial_board_mutation(self):
        from fosgen_pets.draughts import legal_moves
        await self.dispatch(message('/pet_checkers',chat=OWNER))
        board=[0]*64
        for square,piece in ((42,1),(35,-1),(21,-1)):
            board[square]=piece
        self.games.game=replace(self.games.game,board=tuple(board))
        moves=legal_moves(tuple(board),1)
        self.assertEqual(moves[0].path,(42,28,14))
        await self.dispatch(self.cb('42'))
        await self.dispatch(self.cb('28'))
        self.assertEqual(self.games.game.selected,(42,28))
        self.assertEqual(self.games.game.board,tuple(board))
        await self.dispatch(message('/pet_checkers',chat=OWNER))
        self.assertEqual(self.games.game.selected,(42,28))
        await self.dispatch(self.cb('14'))
        self.assertEqual(self.games.game.status,'white_won')
        self.assertEqual(self.games.game.selected,())
        self.assertEqual(self.games.game.board[14],1)
        self.assertEqual(sum(x != 0 for x in self.games.game.board),1)

    async def test_pet_search_runs_off_event_loop_and_resumes_black_turn(self):
        import threading
        from fosgen_pets import draughts
        await self.dispatch(message('/pet_checkers',chat=OWNER))
        self.games.game=replace(self.games.game,side=-1)
        loop_thread=threading.get_ident()
        searched=[]
        def choose(board,side,**kwargs):
            searched.append((threading.get_ident(),kwargs))
            return draughts.legal_moves(board,side)[0]
        with patch.object(draughts,'choose_move',side_effect=choose):
            await self.dispatch(message('/pet_checkers',chat=OWNER))
        self.assertEqual(self.games.game.side,1)
        self.assertNotEqual(searched[0][0],loop_thread)
        self.assertEqual(searched[0][1]['time_limit'],0.7)

    async def test_thirty_quiet_king_plies_draw_and_pawn_resets_counter(self):
        from fosgen_pets import draughts
        await self.dispatch(message('/pet_checkers',chat=OWNER))
        board=[0]*64
        board[56]=2
        board[7]=-2
        self.games.game=replace(self.games.game,board=tuple(board),no_progress=29)
        move=next(m for m in draughts.legal_moves(tuple(board),1) if m.path[-1]==49)
        game=await self.service._move(self.games.game,move)
        self.assertEqual(game.status,'draw')
        self.assertEqual(game.no_progress,30)
        board[56]=1
        self.games.game=replace(game,board=tuple(board),side=1,status='active',no_progress=29)
        game=await self.service._move(self.games.game,draughts.legal_moves(tuple(board),1)[0])
        self.assertEqual(game.no_progress,0)
        self.assertEqual(game.status,'active')

    async def test_repetition_three_draws(self):
        from fosgen_pets import draughts
        from fosgen_pets.draughts_telegram import _key
        await self.dispatch(message('/pet_checkers',chat=OWNER))
        board=[0]*64
        board[56]=2
        board[7]=-2
        game=replace(self.games.game,board=tuple(board))
        move=draughts.legal_moves(game.board,1)[0]
        target=draughts.apply_move(game.board,move)
        self.games.game=replace(game,repetitions={_key(target,-1):2})
        game=await self.service._move(self.games.game,move)
        self.assertEqual(game.status,'draw')

    async def test_ai_result_loses_cas_after_new_board_binding(self):
        from fosgen_pets import draughts
        await self.dispatch(message('/pet_checkers',chat=OWNER))
        self.games.game=replace(self.games.game,side=-1)
        old=self.games.game
        def choose(board,side,**kwargs):
            self.games.game=replace(self.games.game,revision=self.games.game.revision+1,message_id=999)
            return draughts.legal_moves(board,side)[0]
        with patch.object(draughts,'choose_move',side_effect=choose):
            result=await self.service._pet_turn(old)
        self.assertIsNone(result)
        self.assertEqual(self.games.game.board,old.board)
        self.assertEqual(self.games.game.message_id,999)

    async def test_irreversible_move_clears_old_repetition_history(self):
        from fosgen_pets import draughts
        await self.dispatch(message('/pet_checkers',chat=OWNER))
        old={'old-position':2000}
        self.games.game=replace(self.games.game,repetitions=old)
        game=await self.service._move(self.games.game,draughts.legal_moves(self.games.game.board,1)[0])
        self.assertNotIn('old-position',game.repetitions)
        self.assertEqual(len(game.repetitions),1)

    async def test_confirmed_new_game_uses_atomic_expected_revision(self):
        await self.dispatch(message('/pet_checkers',chat=OWNER))
        callback=self.cb('yes')
        revision=self.games.game.revision
        await self.dispatch(callback)
        self.assertEqual(self.games.game.revision,1)
        self.assertEqual(self.games.game.selected,())
        self.assertGreater(self.games.writes,2)

    async def test_late_confirmation_response_repairs_newer_board(self):
        """A Telegram edit can finish after a newer turn was already displayed."""
        await self.dispatch(message('/pet_checkers',chat=OWNER))
        original=self.session.make_request
        delayed=True
        async def request(bot,method,timeout=None):
            nonlocal delayed
            if isinstance(method,EditMessageText) and method.text.startswith('Начать новую') and delayed:
                delayed=False
                latest=await self.service._save(self.games.game,selected=(40,))
                board=message(None,owner=777,chat=OWNER).model_copy(update={'message_id':901}).as_(self.bot)
                await self.service._edit(board,latest)
            return await original(bot,method,timeout)
        with patch.object(self.session,'make_request',side_effect=request):
            await self.dispatch(self.cb('new'))
        edits=[x for x in self.session.requests if isinstance(x,EditMessageText)]
        self.assertFalse(edits[-1].text.startswith('Начать новую'))
        self.assertEqual(self.games.game.selected,(40,))
        self.assertTrue(all(f':{self.games.game.revision}:' in b.callback_data
                            for row in edits[-1].reply_markup.inline_keyboard for b in row))

    async def test_edit_of_old_game_repairs_atomic_replacement_on_same_message(self):
        await self.dispatch(message('/pet_checkers',chat=OWNER))
        original=self.session.make_request
        delayed=True
        async def request(bot,method,timeout=None):
            nonlocal delayed
            if isinstance(method,EditMessageText) and delayed:
                delayed=False
                self.games.game=replace(self.games.game,id='new-game',revision=0,selected=())
            return await original(bot,method,timeout)
        board=message(None,owner=777,chat=OWNER).model_copy(update={'message_id':901}).as_(self.bot)
        with patch.object(self.session,'make_request',side_effect=request):
            await self.service._edit(board,self.games.game)
        edits=[x for x in self.session.requests if isinstance(x,EditMessageText)]
        self.assertTrue(all(':new-game:' in b.callback_data
                            for row in edits[-1].reply_markup.inline_keyboard for b in row))

    async def test_continuous_concurrent_edits_leave_explicit_resume_action(self):
        await self.dispatch(message('/pet_checkers',chat=OWNER))
        original=self.session.make_request
        async def request(bot,method,timeout=None):
            if isinstance(method,EditMessageText):
                self.games.game=replace(self.games.game,revision=self.games.game.revision+1)
            return await original(bot,method,timeout)
        board=message(None,owner=777,chat=OWNER).model_copy(update={'message_id':901}).as_(self.bot)
        before=len(self.session.requests)
        with patch.object(self.session,'make_request',side_effect=request):
            await self.service._edit(board,self.games.game)
        edits=[x for x in self.session.requests[before:] if isinstance(x,EditMessageText)]
        self.assertEqual(len(edits),5)
        self.assertIn('/pet_checkers',edits[-1].text)
        self.assertIsNone(edits[-1].reply_markup)

    async def test_old_message_edit_does_not_overwrite_new_message_binding(self):
        await self.dispatch(message('/pet_checkers',chat=OWNER))
        old=self.games.game
        self.games.game=replace(old,message_id=902,revision=old.revision+1)
        board=message(None,owner=777,chat=OWNER).model_copy(update={'message_id':901}).as_(self.bot)
        before=len(self.session.requests)
        await self.service._edit(board,old)
        self.assertEqual(len(self.session.requests),before)

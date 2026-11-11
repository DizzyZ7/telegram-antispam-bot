"""Exercise the scoped rewrite and real Telegram-command boundaries, no live sends."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock

from aiogram import Dispatcher
from aiogram.types import Message, Update

from writers_moderation import register_writers_chat_handlers, promote_writers_moderation_handler
from writers_meme_rewrite import MemeStickerStore, register_writers_meme_handlers

CHAT = -1002619489118
STICKER = 'CAACAgIAAxkBAAAB1234567890'


def message(text='Я могнул, а они могнули!', *, chat=CHAT, thread=10, mid=77, **extra):
    chat_type = extra.pop('chat_type', 'supergroup')
    data = dict(message_id=mid, date=1, chat=dict(id=chat, type=chat_type),
                from_user=dict(id=7, is_bot=False, first_name='Tester'),
                message_thread_id=thread, text=text)
    data.update(extra)
    return Message.model_validate(data)


class MemeRewriteTests(unittest.IsolatedAsyncioTestCase):
    async def test_other_group_uses_its_own_sticker_and_rewrites_without_global_allowlist(self):
        await self.store.set(-123, 'CAACAgIAAxkBAAAB9876543210')
        await self.middleware(self.next, message(chat=-123), {})
        self.bot.delete_message.assert_awaited_once_with(chat_id=-123, message_id=77)
        self.assertEqual(self.bot.send_sticker.await_args.kwargs['sticker'], 'CAACAgIAAxkBAAAB9876543210')
        self.bot.send_message.assert_not_awaited()
        self.assertNotIn(-123, self.app.ALLOWED_CHATS)

    async def test_other_group_admin_can_use_setting_commands(self):
        await self.command('mog_sticker_status', message('/mog_sticker_status', chat=-123))
        self.assertIn('не настроен', self.bot.send_message.await_args.kwargs['text'])
        self.bot.get_chat_member.assert_awaited_once_with(chat_id=-123, user_id=7)

    async def test_lexicon_only_chat_keeps_profanity_guard_without_writers_topic_exemptions(self):
        target = message('могнул, блядь', chat=-1002659916114, thread=14637)
        self.assertEqual(await self.middleware(self.next, target, {}), 'continued')
        self.bot.delete_message.assert_not_awaited()

    async def test_other_group_meme_with_profanity_is_deleted_without_unsafe_copy(self):
        await self.middleware(self.next, message('могнул, блядь', chat=-123), {})
        self.bot.delete_message.assert_awaited_once()
        self.bot.send_message.assert_not_awaited()

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = MemeStickerStore(Path(self.temp.name))
        await self.store.set(CHAT, STICKER)
        self.bot = Obj(**{name: AsyncMock() for name in (
            'delete_message', 'send_message', 'send_sticker', 'send_photo', 'send_video',
            'send_animation', 'send_audio', 'send_voice', 'send_document', 'get_chat_member')})
        self.bot.id = 999
        self.bot.get_chat_member.return_value = Obj(status='administrator')
        self.app = Obj(bot=self.bot, dp=Dispatcher(), ALLOWED_CHATS=[])
        self.scope = register_writers_chat_handlers(self.app)
        self.scope.chat_id = CHAT
        self.purge = AsyncMock()
        self.middleware = register_writers_meme_handlers(
            self.app, self.scope, self.store, on_message_deleted=self.purge)
        self.next = AsyncMock(return_value='continued')

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def command(self, name, target):
        promote_writers_moderation_handler(self.app.dp)
        record = next(r for r in self.app.dp.message.handlers if r.callback.__name__ == name)
        accepted, _ = await record.check(target, bot=self.bot)
        self.assertTrue(accepted)
        await record.callback(target)

    async def test_delete_then_purge_sticker_and_plain_replacement_in_same_topic(self):
        order = []
        for name in ('delete_message', 'send_sticker', 'send_message'):
            getattr(self.bot, name).side_effect = lambda _name=name, **kw: order.append((_name, kw))
        await self.middleware(self.next, message('Я <b>могнул</b>!'), {})
        self.assertEqual([name for name, _ in order], ['delete_message', 'send_sticker'])
        self.assertEqual(order[1][1]['sticker'], STICKER)
        for _, params in order[1:]:
            self.assertEqual(params['message_thread_id'], 10)
            self.assertNotIn('reply_to_message_id', params)
        self.purge.assert_awaited_once_with(CHAT, 77)
        self.next.assert_not_awaited()

    async def test_scope_similar_words_bots_and_existing_profanity_continue(self):
        cases = [message(chat=7, chat_type='private'), message('я могучий'), message('я могнул, блядь'),
                 message(from_user=dict(id=99, is_bot=True, first_name='Bot'))]
        for target in cases:
            self.assertEqual(await self.middleware(self.next, target, {}), 'continued')
        self.bot.delete_message.assert_not_awaited()
        self.bot.send_message.assert_not_awaited()

    async def test_failed_delete_never_purges_or_reposts(self):
        self.bot.delete_message.side_effect = RuntimeError('no rights')
        await self.middleware(self.next, message(), {})
        self.purge.assert_not_awaited()
        self.bot.send_message.assert_not_awaited()
        self.bot.send_sticker.assert_not_awaited()
        self.next.assert_not_awaited()

    async def test_sticker_failure_and_memory_failure_still_return_text_without_retry(self):
        self.bot.send_sticker.side_effect = TimeoutError('delivery uncertain')
        self.purge.side_effect = RuntimeError('database down')
        await self.middleware(self.next, message(), {})
        self.bot.send_sticker.assert_awaited_once()
        self.bot.send_message.assert_not_awaited()

    async def test_replay_and_concurrent_update_do_not_duplicate_send(self):
        import asyncio
        await asyncio.gather(*(self.middleware(self.next, message(), {}) for _ in range(3)))
        self.bot.delete_message.assert_awaited_once()
        self.bot.send_message.assert_not_awaited()
        self.bot.send_sticker.assert_awaited_once()

    async def test_photo_is_restored_with_corrected_plain_caption(self):
        target = message(None, caption='Кот могнул!', photo=[dict(file_id='photo-id',
                         file_unique_id='photo-unique', width=10, height=10)])
        await self.middleware(self.next, target, {})
        params = self.bot.send_photo.await_args.kwargs
        self.bot.send_photo.assert_not_awaited()
        self.bot.send_message.assert_not_awaited()

    async def test_no_sticker_config_and_no_thread_still_return_corrected_text(self):
        middleware = register_writers_meme_handlers(
            Obj(bot=self.bot, dp=Dispatcher()), self.scope,
            MemeStickerStore(Path(self.temp.name) / 'empty'))
        await middleware(self.next, message(thread=None), {})
        self.bot.send_sticker.assert_not_awaited()
        self.assertNotIn('message_thread_id', self.bot.send_message.await_args.kwargs)
        self.bot.send_message.assert_not_awaited()

    async def test_unsafe_rest_of_message_is_not_republished(self):
        await self.middleware(self.next, message('могнул, какой-то идиот опять тут'), {})
        self.bot.delete_message.assert_awaited_once()
        self.bot.send_sticker.assert_awaited_once()
        self.bot.send_message.assert_not_awaited()

    async def test_long_replacement_preserves_all_text_across_bounded_parts(self):
        await self.middleware(self.next, message('могнул ' * 580), {})
        parts = [c.kwargs['text'] for c in self.bot.send_message.await_args_list]
        self.assertEqual(parts, [])

    async def test_admin_can_install_from_replied_sticker_and_status_survives_restart(self):
        target = message('/set_mog_sticker', reply_to_message=dict(
            message_id=1, date=1, chat=dict(id=CHAT, type='supergroup'),
            sticker=dict(file_id=STICKER, file_unique_id='sticker-unique',
                         type='regular', width=100, height=100, is_animated=False, is_video=False)))
        await self.command('set_mog_sticker', target)
        fresh = MemeStickerStore(Path(self.temp.name))
        self.assertEqual(await fresh.get(CHAT), STICKER)
        self.assertIsNone(await fresh.get(-123))
        await self.command('mog_sticker_status', message('/mog_sticker_status'))
        self.assertIn('настроен', self.bot.send_message.await_args.kwargs['text'])

    async def test_unauthorized_or_unverifiable_admin_cannot_change_configuration(self):
        self.bot.get_chat_member.return_value = Obj(status='member')
        await self.command('set_mog_sticker', message('/set_mog_sticker'))
        self.assertEqual(await self.store.get(CHAT), STICKER)
        self.assertIn('администратор', self.bot.send_message.await_args.kwargs['text'])
        self.bot.get_chat_member.side_effect = RuntimeError('permission check failed')
        await self.command('set_mog_sticker', message('/set_mog_sticker'))
        self.assertEqual(await self.store.get(CHAT), STICKER)

    async def test_edited_message_middleware_is_registered_and_deletes_new_meme(self):
        chain = list(self.app.dp.edited_message.outer_middleware)
        self.assertIn(self.middleware, chain)
        await chain[chain.index(self.middleware)](self.next, message(), {})
        self.bot.delete_message.assert_awaited_once()

    async def test_polling_subscribes_to_edits_and_real_update_is_rewritten(self):
        # Production passes this discovery result to start_polling. Middleware
        # alone is insufficient to subscribe aiogram to edited_message updates.
        self.assertIn('edited_message', self.app.dp.resolve_used_update_types())
        target = message('Кот могнул!', edit_date=2)
        update = Update.model_validate(
            {'update_id': 1, 'edited_message': target.model_dump(mode='json', exclude_none=True)},
            context={'bot': self.bot})
        await self.app.dp.feed_update(self.bot, update)
        self.bot.delete_message.assert_awaited_once_with(chat_id=CHAT, message_id=77)
        self.bot.send_message.assert_not_awaited()
        self.bot.send_sticker.assert_awaited_once()

    async def test_edited_meme_with_profanity_is_deleted_even_without_edited_profanity_handler(self):
        await self.middleware(self.next, message('могнул, блядь', edit_date=2), {})
        self.bot.delete_message.assert_awaited_once()
        self.bot.send_message.assert_not_awaited()
        self.next.assert_not_awaited()

    async def test_long_caption_keeps_media_and_entire_corrected_text(self):
        target = message(None, caption='могнул ' * 145, photo=[dict(file_id='photo-id',
                         file_unique_id='photo-unique', width=10, height=10)])
        await self.middleware(self.next, target, {})
        self.bot.send_photo.assert_not_awaited()
        self.bot.send_message.assert_not_awaited()

    async def test_uncertain_text_delivery_is_never_retried_on_replay(self):
        self.bot.send_sticker.side_effect = TimeoutError('unknown Telegram result')
        await self.middleware(self.next, message(), {})
        await self.middleware(self.next, message(), {})
        self.bot.send_message.assert_not_awaited()
        self.bot.send_sticker.assert_awaited_once()

    async def test_per_chat_sticker_cooldown_does_not_limit_deletion(self):
        await self.middleware(self.next, message(mid=77), {})
        await self.middleware(self.next, message(mid=78), {})
        self.assertEqual(self.bot.delete_message.await_count, 2)
        self.bot.send_sticker.assert_awaited_once()
        self.bot.send_message.assert_not_awaited()

    async def test_cooldown_is_independent_between_groups(self):
        await self.store.set(-123, STICKER)
        await self.middleware(self.next, message(mid=77), {})
        await self.middleware(self.next, message(mid=78, chat=-123), {})
        self.assertEqual(self.bot.send_sticker.await_count, 2)
        self.assertEqual(self.bot.delete_message.await_count, 2)

    async def test_missing_sticker_does_not_suppress_future_sticker(self):
        await self.middleware(self.next, message(mid=77, chat=-123), {})
        self.bot.send_sticker.assert_not_awaited()
        await self.store.set(-123, STICKER)
        await self.middleware(self.next, message(mid=78, chat=-123), {})
        self.bot.send_sticker.assert_awaited_once()

    async def test_private_commands_cannot_read_config_or_lookup_admin(self):
        for name, text in (('set_mog_sticker', '/set_mog_sticker'),
                           ('mog_sticker_status', '/mog_sticker_status')):
            record = next(r for r in self.app.dp.message.handlers if r.callback.__name__ == name)
            accepted, _ = await record.check(message(text, chat=7, chat_type='private'), bot=self.bot)
            self.assertFalse(accepted)
        self.bot.get_chat_member.assert_not_awaited()
        self.bot.send_message.assert_not_awaited()

    async def test_moderated_word_never_reaches_learning_outer_middleware(self):
        import asyncio
        from entertainment.router import EntertainmentLearningMiddleware
        observer = Obj(chat_ids={CHAT}, search_answers=None, observe_message=AsyncMock())
        learning = EntertainmentLearningMiddleware(observer)
        self.app.dp.message.outer_middleware(learning)
        await self.app.dp.message.wrap_outer_middleware(self.next, message(), {})
        await asyncio.sleep(0)
        observer.observe_message.assert_not_awaited()
        self.bot.send_message.assert_not_awaited()

    async def test_anonymous_admin_message_is_moderated_but_cannot_configure(self):
        target = message(from_user=dict(id=99, is_bot=True, first_name='Anonymous'),
                         sender_chat=dict(id=CHAT, type='supergroup'))
        await self.middleware(self.next, target, {})
        self.bot.delete_message.assert_awaited_once()
        await self.command('set_mog_sticker', target.model_copy(update={'text': '/set_mog_sticker'}))
        self.bot.get_chat_member.assert_not_awaited()


class MemeStickerStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_postgres_is_source_of_truth_and_failed_write_preserves_cached_value(self):
        db = Obj(get_meme_sticker=AsyncMock(return_value=STICKER),
                 set_meme_sticker=AsyncMock(side_effect=RuntimeError('db down')))
        with tempfile.TemporaryDirectory() as root:
            store = MemeStickerStore(Path(root), postgres=db)
            self.assertEqual(await store.get(CHAT), STICKER)
            with self.assertRaises(RuntimeError):
                await store.set(CHAT, 'CAACAgIAAxkBAAAB9876543210')
            self.assertEqual(await store.get(CHAT), STICKER)
            self.assertEqual(list(Path(root).iterdir()), [])

    async def test_invalid_setting_does_not_overwrite_valid_file(self):
        with tempfile.TemporaryDirectory() as root:
            store = MemeStickerStore(Path(root))
            await store.set(CHAT, STICKER)
            with self.assertRaises(ValueError):
                await store.set(CHAT, '<b>invalid</b>')
            fresh = MemeStickerStore(Path(root))
            self.assertEqual(await fresh.get(CHAT), STICKER)

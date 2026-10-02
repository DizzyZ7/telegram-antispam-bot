from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from entertainment.models import EntertainmentSettings, MemoryEventType
from entertainment.service import EntertainmentService


def obj(**kwargs):
    return SimpleNamespace(**kwargs)


def make_message(
    *,
    chat_id: int = -1001,
    topic_id: int = 10,
    user_id: int = 7,
    is_bot: bool = False,
    text: str | None = "нормальное человеческое сообщение для памяти",
    sticker=None,
    photo=None,
    animation=None,
    message_id: int = 500,
):
    return obj(
        chat=obj(id=chat_id, type="supergroup"),
        from_user=obj(id=user_id, is_bot=is_bot),
        message_thread_id=topic_id,
        message_id=message_id,
        text=text,
        caption=None,
        sticker=sticker,
        photo=photo,
        animation=animation,
        voice=None,
        video=None,
        document=None,
        reply_to_message=None,
        forward_origin=None,
        forward_date=None,
        replies=[],
        reply=AsyncMock(),
    )


def make_storage(*, remember: bool = True, enabled: bool = True):
    storage = SimpleNamespace(
        get_settings=AsyncMock(return_value=EntertainmentSettings(enabled=enabled)),
        get_remember_enabled=AsyncMock(return_value=remember),
        add_event=AsyncMock(return_value=1),
        add_message=AsyncMock(),
        set_remember_enabled=AsyncMock(),
        delete_user_memory=AsyncMock(return_value=3),
        delete_legacy_user_messages=AsyncMock(return_value=2),
    )
    return storage


class CultureMemoryServiceTests(unittest.IsolatedAsyncioTestCase):
    def service(self, storage) -> EntertainmentService:
        app = obj(bot=obj(id=999))
        service = EntertainmentService(app, storage, {-1001}, now_fn=lambda: 12345.0)
        service.evaluate_topic = AsyncMock(return_value=None)
        return service

    async def test_eligible_text_dual_writes_and_keeps_existing_autonomy_path(self):
        storage = make_storage()
        service = self.service(storage)
        message = make_message()

        await service.observe_message(message)

        storage.add_event.assert_awaited_once()
        event = storage.add_event.await_args.args[0]
        self.assertEqual(event.event_type, MemoryEventType.TEXT)
        self.assertEqual(event.text, message.text)
        storage.add_message.assert_awaited_once_with(
            chat_id=-1001,
            topic_id=10,
            user_id=7,
            text=message.text,
            message_id=500,
            created_at=12345,
        )
        service.evaluate_topic.assert_awaited_once_with(message)
        self.assertIn((-1001, 10), service._active_topics)

    async def test_emoji_only_is_remembered_without_legacy_activity_or_autonomy(self):
        storage = make_storage()
        service = self.service(storage)
        message = make_message(text="😂🔥")

        await service.observe_message(message)

        event = storage.add_event.await_args.args[0]
        self.assertEqual(event.event_type, MemoryEventType.EMOJI)
        storage.add_message.assert_not_awaited()
        service.evaluate_topic.assert_not_awaited()
        self.assertNotIn((-1001, 10), service._active_topics)

    async def test_sticker_photo_and_animation_are_canonical_only(self):
        cases = [
            (
                make_message(
                    text=None,
                    message_id=601,
                    sticker=obj(
                        file_id="sticker-file",
                        file_unique_id="sticker-unique",
                        emoji="😼",
                        set_name="cats",
                        width=512,
                        height=512,
                        is_animated=False,
                        is_video=False,
                    ),
                ),
                MemoryEventType.STICKER,
            ),
            (
                make_message(
                    text=None,
                    message_id=602,
                    photo=[obj(file_id="photo-file", file_unique_id="photo-unique", width=800, height=600, file_size=100)],
                ),
                MemoryEventType.PHOTO,
            ),
            (
                make_message(
                    text=None,
                    message_id=603,
                    animation=obj(file_id="gif-file", file_unique_id="gif-unique", width=640, height=360, duration=3),
                ),
                MemoryEventType.ANIMATION,
            ),
        ]
        for message, expected_type in cases:
            with self.subTest(expected_type=expected_type):
                storage = make_storage()
                service = self.service(storage)
                await service.observe_message(message)
                event = storage.add_event.await_args.args[0]
                self.assertEqual(event.event_type, expected_type)
                storage.add_message.assert_not_awaited()
                service.evaluate_topic.assert_not_awaited()

    async def test_opt_out_blocks_canonical_and_legacy_writes(self):
        storage = make_storage(remember=False)
        service = self.service(storage)
        await service.observe_message(make_message())

        storage.add_event.assert_not_awaited()
        storage.add_message.assert_not_awaited()
        service.evaluate_topic.assert_not_awaited()

    async def test_bot_unsupported_chat_and_unsupported_media_are_ignored(self):
        for message in (
            make_message(is_bot=True),
            make_message(chat_id=-9999),
            make_message(text=None),
        ):
            with self.subTest(message_id=message.message_id, chat_id=message.chat.id):
                storage = make_storage()
                service = self.service(storage)
                await service.observe_message(message)
                storage.add_event.assert_not_awaited()
                storage.add_message.assert_not_awaited()

    async def test_disabled_entertainment_does_not_learn(self):
        storage = make_storage(enabled=False)
        service = self.service(storage)
        await service.observe_message(make_message())
        storage.add_event.assert_not_awaited()
        storage.add_message.assert_not_awaited()

    async def test_short_text_is_canonical_but_not_legacy(self):
        storage = make_storage()
        service = self.service(storage)
        await service.observe_message(make_message(text="ага"))
        event = storage.add_event.await_args.args[0]
        self.assertEqual(event.event_type, MemoryEventType.TEXT)
        storage.add_message.assert_not_awaited()
        service.evaluate_topic.assert_not_awaited()

    async def test_privacy_methods_delegate_to_storage_without_sql(self):
        storage = make_storage()
        service = self.service(storage)
        message = make_message(user_id=77)

        await service.set_remember_me(message, False)
        storage.set_remember_enabled.assert_awaited_once_with(-1001, 77, False)

        deleted = await service.delete_my_memory(message)
        storage.delete_user_memory.assert_awaited_once_with(-1001, 77)
        storage.delete_legacy_user_messages.assert_awaited_once_with(-1001, 77)
        self.assertEqual(deleted, 5)


if __name__ == "__main__":
    unittest.main()

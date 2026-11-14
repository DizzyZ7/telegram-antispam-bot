"""Pets routing and post-moderation counting through a real aiogram dispatcher."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.dispatcher.event.handler import HandlerObject
from aiogram.methods import AnswerCallbackQuery, GetMe, SendMessage, SendPhoto
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from fosgen_pets.core import build_appearance


NOW = datetime.now(timezone.utc).replace(microsecond=0)
OWNER = 11
GROUP = -100123


class RecordingSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.requests = []

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):
        if False:
            yield b""

    async def make_request(self, bot, method, timeout=None):
        self.requests.append(method)
        if isinstance(method, GetMe):
            return User(id=777, is_bot=True, first_name="Фосген", username="FosgenBot")
        if isinstance(method, AnswerCallbackQuery):
            return True
        if isinstance(method, (SendMessage, SendPhoto)):
            return Message(message_id=901, date=NOW, chat=Chat(id=int(method.chat_id), type="private"))
        raise AssertionError(f"Unexpected Telegram operation {type(method).__name__}")


def pet_fixture():
    return SimpleNamespace(
        id=9, owner_id=OWNER, appearance=build_appearance("kitten", "telegram-fixture"),
        svg="<svg/>", png=b"\x89PNG\r\n\x1a\nfrozen-art", pixel_hash="a" * 64,
        name="<Кнопка>", origin_story="История питомца", created_at=NOW,
        mood="calm", energy=80, satiety=90, renderer_version="fosgen-svg-v2",
    )


class BoundaryStore:
    """Only the external PostgreSQL boundary is replaced in dispatcher tests."""
    def __init__(self):
        self.pet = None
        self.count = 0
        self.opted_in = False
        self.calls = []
        self.failure = None
        self.care_reason = "applied"

    async def opt_in(self, owner_id, now=None):
        self.calls.append(("opt_in", owner_id))
        self.opted_in = True
        return await self.progress(owner_id)

    async def revoke(self, owner_id):
        self.calls.append(("revoke", owner_id))
        self.opted_in = False

    async def progress(self, owner_id):
        self.calls.append(("progress", owner_id))
        return SimpleNamespace(count=self.count, opted_in=self.opted_in, counted=False)

    async def get(self, owner_id):
        self.calls.append(("get", owner_id))
        if self.failure:
            raise self.failure
        return self.pet

    async def adopt(self, owner_id, appearance_factory=None):
        self.calls.append(("adopt", owner_id))
        if self.failure:
            raise self.failure
        if self.count >= 100:
            self.pet = self.pet or pet_fixture()
        return self.pet

    async def record_message(self, eligibility, now=None):
        self.calls.append(("record", eligibility, now))
        if self.failure:
            raise self.failure
        return SimpleNamespace(count=self.count, opted_in=self.opted_in, counted=False)

    async def care(self, owner_id, action, request_id, now=None):
        self.calls.append(("care", owner_id, action, request_id))
        return SimpleNamespace(pet=self.pet, applied=self.care_reason == "applied",
                               reason=self.care_reason, retry_after=30)

    async def history(self, owner_id, limit=10):
        self.calls.append(("history", owner_id, limit))
        return [{"event_type": "care:feed", "created_at": NOW,
                 "event_data": {"untrusted": "RAW MESSAGE MUST NOT APPEAR"}}]


def message(text="Сегодня хороший день", *, owner=OWNER, chat=GROUP, **fields):
    return Message(message_id=21, date=NOW,
                   chat=Chat(id=chat, type="private" if chat > 0 else "supergroup"),
                   from_user=User(id=owner, first_name="User", is_bot=False), text=text, **fields)


class TelegramPetsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from fosgen_pets.telegram import register_pet_handlers
        self.session = RecordingSession()
        self.bot = Bot("777:" + "a" * 35, session=self.session,
                       default=DefaultBotProperties(parse_mode="HTML"))
        self.addAsyncCleanup(self.bot.session.close)
        self.app = SimpleNamespace(dp=Dispatcher(), ALLOWED_CHATS=[GROUP], pending_users={})
        self.store = BoundaryStore()
        self.collected = []

        async def collect_text_messages(message):
            self.collected.append(message.message_id)
            return "legacy-result"

        self.collect_text_messages = collect_text_messages
        self.app.collect_text_messages = collect_text_messages
        # This catch-all exists before the feature is registered in production.
        self.app.dp.message.register(collect_text_messages, F.text)
        self.service = register_pet_handlers(self.app, self.store, chat_ids=frozenset({GROUP, -999}))
        self.update_id = 0

    async def dispatch(self, event):
        self.update_id += 1
        field = "callback_query" if isinstance(event, CallbackQuery) else "message"
        return await self.app.dp.feed_update(self.bot, Update(update_id=self.update_id, **{field: event}))

    def texts(self):
        return [call.text for call in self.session.requests if isinstance(call, SendMessage)]

    def records(self):
        return [call for call in self.store.calls if call[0] == "record"]

    def callback(self, data, *, owner=OWNER, chat=OWNER):
        return CallbackQuery(id="care-request-1", from_user=User(id=owner, first_name="User", is_bot=False),
                             chat_instance="instance", data=data,
                             message=message(None, owner=777, chat=chat))

    async def test_explicit_consent_command_routes_before_existing_catchall(self):
        await self.dispatch(message("/pet_start@FosgenBot", chat=OWNER))
        self.assertTrue(self.store.opted_in)
        self.assertEqual(self.collected, [])
        text = "\n".join(self.texts())
        for required in ("100", "после согласия", "навсегда", "не умирает"):
            self.assertIn(required, text.lower())
        self.assertNotIn("<b>", text)
        self.assertNotIn("ё", text)
        self.assertEqual(self.records(), [])

    async def test_unrelated_group_text_reaches_legacy_and_counts_after_success(self):
        result = await self.dispatch(message())
        self.assertEqual(result, "legacy-result")
        self.assertEqual(self.collected, [21])
        self.assertEqual(len(self.records()), 1)
        _, recorded, source_time = self.records()[0]
        self.assertEqual((recorded.user_id, recorded.chat_id, recorded.message_id), (OWNER, GROUP, 21))
        self.assertTrue(recorded.opted_in)
        self.assertEqual(source_time, NOW)
        self.assertEqual(self.session.requests, [])

    async def test_group_and_mismatched_private_commands_do_not_grant_consent(self):
        for event in (message("/pet_start"), message("/pet_start", chat=99)):
            await self.dispatch(event)
        self.assertFalse(self.store.opted_in)
        self.assertEqual(len(self.collected), 2)
        self.assertEqual(self.records(), [])

    async def test_unconsented_pet_command_is_read_only_and_explains_start(self):
        await self.dispatch(message("/pet", chat=OWNER))
        self.assertFalse(self.store.opted_in)
        self.assertNotIn("adopt", [call[0] for call in self.store.calls])
        self.assertIn("/pet_start", "\n".join(self.texts()))

    async def test_existing_pet_uses_saved_png_and_own_care_keyboard(self):
        self.store.pet = pet_fixture()
        await self.dispatch(message("/pet", chat=OWNER))
        photos = [call for call in self.session.requests if isinstance(call, SendPhoto)]
        self.assertEqual(len(photos), 1)
        self.assertEqual(photos[0].photo.data, b"\x89PNG\r\n\x1a\nfrozen-art")
        self.assertIsNone(photos[0].parse_mode)
        self.assertIn("спокойное", photos[0].caption)
        self.assertIn("80", photos[0].caption)
        self.assertIn("90", photos[0].caption)
        buttons = [b.callback_data for row in photos[0].reply_markup.inline_keyboard for b in row]
        self.assertEqual(set(buttons), {"petcare:9:feed", "petcare:9:play", "petcare:9:sleep",
                                        "petview:9:passport", "petview:9:history", "petview:9:story"})
        self.assertNotIn("adopt", [call[0] for call in self.store.calls])

    async def test_100th_progress_adopts_lazily_and_persists_single_identity(self):
        self.store.count = 100
        self.store.opted_in = True
        await self.dispatch(message("/pet", chat=OWNER))
        first = self.store.pet
        await self.dispatch(message("/pet", chat=OWNER))
        self.assertIs(self.store.pet, first)
        self.assertEqual(sum(call[0] == "adopt" for call in self.store.calls), 1)

    async def test_old_unavailable_art_is_explicit_and_never_adopted_again(self):
        self.store.pet = pet_fixture()
        self.store.pet.png = b""
        await self.dispatch(message("/pet", chat=OWNER))
        self.assertFalse(any(isinstance(call, SendPhoto) for call in self.session.requests))
        self.assertIn("изображение", "\n".join(self.texts()).lower())
        self.assertNotIn("adopt", [call[0] for call in self.store.calls])

    async def test_pause_preserves_existing_pet_and_progress(self):
        self.store.opted_in = True
        self.store.count = 78
        self.store.pet = pet_fixture()
        pet = self.store.pet
        await self.dispatch(message("/pet_pause", chat=OWNER))
        self.assertFalse(self.store.opted_in)
        self.assertEqual(self.store.count, 78)
        self.assertIs(self.store.pet, pet)
        self.assertIn("сохран", "\n".join(self.texts()).lower())

    async def test_passport_escapes_canonical_dna_and_displays_version_seed_hash(self):
        self.store.pet = pet_fixture()
        self.store.pet.appearance = replace(self.store.pet.appearance, species="<script>")
        await self.dispatch(message("/pet_passport", chat=OWNER))
        text = "\n".join(self.texts())
        self.assertIn("&lt;script&gt;", text)
        self.assertNotIn("<script>", text)
        self.assertIn(str(self.store.pet.appearance.pattern_seed), text)
        self.assertIn("a" * 64, text)
        self.assertIn("<code>", text)

    async def test_history_never_displays_event_metadata(self):
        self.store.pet = pet_fixture()
        await self.dispatch(message("/pet_history", chat=OWNER))
        text = "\n".join(self.texts())
        self.assertIn("корм", text.lower())
        self.assertNotIn("RAW MESSAGE", text)

    async def test_story_shows_saved_origin_without_generating_or_changing_identity(self):
        self.store.pet = pet_fixture()
        self.store.pet.origin_story = "Питомец нашел дом у реки."
        await self.dispatch(message("/pet_story", chat=OWNER))
        self.assertIn("Питомец нашел дом у реки.", "\n".join(self.texts()))
        self.assertNotIn("adopt", [call[0] for call in self.store.calls])
        self.assertEqual(self.store.pet.id, 9)

    async def test_story_button_uses_saved_origin_and_normalizes_legacy_russian_spelling(self):
        self.store.pet = pet_fixture()
        self.store.pet.origin_story = "Питомец нашёл друга."
        await self.dispatch(self.callback("petview:9:story"))
        self.assertIn("Питомец нашел друга.", "\n".join(self.texts()))
        self.assertEqual(self.store.pet.origin_story, "Питомец нашёл друга.")
        self.assertFalse(any(call[0] == "care" for call in self.store.calls))

    async def test_privacy_discloses_metadata_and_pausing_without_reading_database(self):
        await self.dispatch(message("/pet_privacy", chat=OWNER))
        text = "\n".join(self.texts())
        for required in ("HMAC", "/pet_pause", "текст"):
            self.assertIn(required, text)
        self.assertEqual(self.store.calls, [])

    async def test_callback_acknowledges_before_store_and_uses_callback_id_for_idempotence(self):
        self.store.pet = pet_fixture()
        original_get = self.store.get

        async def get_after_ack(owner):
            self.assertTrue(any(isinstance(call, AnswerCallbackQuery) for call in self.session.requests))
            return await original_get(owner)

        self.store.get = get_after_ack
        await self.dispatch(self.callback("petcare:9:feed"))
        self.assertIn(("care", OWNER, "feed", "care-request-1"), self.store.calls)
        self.assertTrue(self.texts())
        self.assertTrue(all(call.parse_mode is None for call in self.session.requests if isinstance(call, SendMessage)))

    async def test_stale_pet_id_never_mutates_pet(self):
        self.store.pet = pet_fixture()
        await self.dispatch(self.callback("petcare:999:feed"))
        self.assertFalse(any(call[0] == "care" for call in self.store.calls))
        self.assertTrue(all(call[1] == OWNER for call in self.store.calls))

    async def test_forged_callback_owner_and_group_context_are_not_consumed(self):
        called = []

        async def fallback(callback):
            called.append(callback.id)

        self.app.dp.callback_query.register(fallback)
        for event in (self.callback("petcare:9:feed", owner=99),
                      self.callback("petcare:9:feed", chat=GROUP)):
            await self.dispatch(event)
        self.assertEqual(called, ["care-request-1", "care-request-1"])
        self.assertEqual(self.store.calls, [])

    async def test_pets_outage_shows_no_exception_or_secrets(self):
        self.store.failure = RuntimeError("postgresql://admin:SECRET@private-host/db")
        with self.assertLogs("fosgen_pets.telegram", level="WARNING") as captured:
            await self.dispatch(message("/pet", chat=OWNER))
        public = "\n".join(self.texts()) + "\n".join(captured.output)
        self.assertNotIn("SECRET", public)
        self.assertNotIn("private-host", public)
        self.assertIn("позже", public)

    async def test_moderation_selected_ahead_of_catchall_never_counts(self):
        deleted = []

        async def moderation(event):
            deleted.append(event.message_id)
            return "moderated"

        self.app.dp.message.register(moderation, F.text == "Сегодня хороший день")
        moderation_handler = next(item for item in self.app.dp.message.handlers if item.callback is moderation)
        self.app.dp.message.handlers.remove(moderation_handler)
        self.app.dp.message.handlers.insert(0, moderation_handler)
        self.assertEqual(await self.dispatch(message()), "moderated")
        self.assertEqual(deleted, [21])
        self.assertEqual(self.collected, [])
        self.assertEqual(self.records(), [])

    async def test_failed_lazy_adoption_retries_without_consuming_progress(self):
        self.store.count = 100
        self.store.opted_in = True
        original_adopt = self.store.adopt

        async def unavailable_adoption(owner):
            raise RuntimeError("rasterizer unavailable")

        self.store.adopt = unavailable_adoption
        with self.assertLogs("fosgen_pets.telegram", level="WARNING"):
            await self.dispatch(message("/pet", chat=OWNER))
        self.assertIsNone(self.store.pet)
        self.assertEqual(self.store.count, 100)
        self.store.adopt = original_adopt
        await self.dispatch(message("/pet", chat=OWNER))
        self.assertIsNotNone(self.store.pet)

    async def test_private_bot_or_sender_chat_command_cannot_opt_in(self):
        for event in (
            message("/pet_start", chat=OWNER).model_copy(update={"from_user": User(id=OWNER, is_bot=True, first_name="bot")}),
            message("/pet_start", chat=OWNER, sender_chat=Chat(id=GROUP, type="supergroup")),
        ):
            await self.dispatch(event)
        self.assertFalse(self.store.opted_in)
        self.assertEqual(self.store.calls, [])

    async def test_callback_passport_and_history_use_the_same_private_owner(self):
        self.store.pet = pet_fixture()
        await self.dispatch(self.callback("petview:9:passport"))
        await self.dispatch(self.callback("petview:9:history"))
        self.assertEqual(sum(isinstance(call, AnswerCallbackQuery) for call in self.session.requests), 2)
        self.assertIn(("history", OWNER, 10), self.store.calls)
        self.assertFalse(any(call[0] == "care" for call in self.store.calls))

    async def test_cooldown_and_duplicate_callbacks_display_persistent_status(self):
        self.store.pet = pet_fixture()
        for reason in ("cooldown", "duplicate"):
            self.store.care_reason = reason
            await self.dispatch(self.callback("petcare:9:play"))
        text = "\n".join(self.texts())
        self.assertIn("30", text)
        self.assertIn("уже учтено", text)
        self.assertEqual(self.store.pet.energy, 80)

    async def test_malformed_or_cross_kind_callbacks_fall_through_without_store_access(self):
        received = []

        async def fallback(callback):
            received.append(callback.data)

        self.app.dp.callback_query.register(fallback)
        payloads = ("petcare:9:passport", "petview:9:feed", "petcare:9999999999999999999:feed",
                    "petcare:9:feed:11", "petcare:-9:play", "petcare:09:feed")
        for payload in payloads:
            await self.dispatch(self.callback(payload))
        self.assertEqual(received, list(payloads))
        self.assertEqual(self.store.calls, [])

    async def test_only_suitable_original_allowed_group_text_can_reach_counting_store(self):
        cases = [
            message("/pettest kitten"), message("/help kittens"), message("hi"),
            message(chat=OWNER), message(chat=-999),
            message(sender_chat=Chat(id=GROUP, type="supergroup")),
            message(edit_date=int(NOW.timestamp())), message(via_bot=User(id=777, is_bot=True, first_name="bot")),
            message(forward_origin={"type": "hidden_user", "date": int(NOW.timestamp()),
                                    "sender_user_name": "Hidden"}),
            message().model_copy(update={"date": NOW + timedelta(minutes=2)}),
            message().model_copy(update={"from_user": User(id=OWNER, is_bot=True, first_name="bot")}),
        ]
        for event in cases:
            with self.subTest(event=event.model_dump(exclude_none=True)):
                await self.dispatch(event)
        self.assertEqual(self.records(), [])


class CountingMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from fosgen_pets.telegram import PetCountingMiddleware
        self.store = BoundaryStore()
        self.app = SimpleNamespace(ALLOWED_CHATS=[GROUP], pending_users={})
        self.middleware = PetCountingMiddleware(self.app, self.store, frozenset({GROUP}))

    async def call(self, handler, event=None, selected=None):
        return await self.middleware(handler, event or message(),
                                     {"handler": HandlerObject(selected or handler)})

    async def test_pending_snapshot_excludes_deleted_captcha_even_if_handler_clears_pending(self):
        self.app.pending_users[OWNER] = "captcha"

        async def collect_text_messages(event, data):
            self.app.pending_users.clear()
            return "deleted"

        self.assertEqual(await self.call(collect_text_messages), "deleted")
        self.assertEqual(self.store.calls, [])

    async def test_moderation_and_unknown_handlers_never_count(self):
        async def moderate(event, data):
            return "deleted"

        self.assertEqual(await self.call(moderate), "deleted")
        self.assertEqual(self.store.calls, [])

    async def test_callback_with_the_collectors_name_cannot_impersonate_known_collector(self):
        async def collect_text_messages(event, data):
            return "foreign collector"

        async def actual_collector(event, data):
            return "known collector"

        self.app.collect_text_messages = actual_collector
        self.assertEqual(await self.call(collect_text_messages), "foreign collector")
        self.assertEqual(self.store.calls, [])

    async def test_legacy_failure_propagates_and_never_counts(self):
        async def collect_text_messages(event, data):
            raise RuntimeError("legacy failure")

        with self.assertRaisesRegex(RuntimeError, "legacy failure"):
            await self.call(collect_text_messages)
        self.assertEqual(self.store.calls, [])

    async def test_pets_outage_does_not_change_legacy_result_or_log_raw_text(self):
        self.store.failure = RuntimeError("SECRET raw text postgres://password")

        async def collect_text_messages(event, data):
            return "saved"

        with self.assertLogs("fosgen_pets.telegram", level="WARNING") as captured:
            self.assertEqual(await self.call(collect_text_messages), "saved")
        self.assertNotIn("SECRET", "\n".join(captured.output))
        self.assertNotIn("Сегодня", "\n".join(captured.output))

    async def test_scope_is_rechecked_when_parent_allowlist_changes(self):
        self.app.ALLOWED_CHATS.clear()

        async def collect_text_messages(event, data):
            return "ignored"

        await self.call(collect_text_messages)
        self.assertEqual(self.store.calls, [])

    async def test_lexicon_only_scope_is_never_counted(self):
        self.app.LEXICON_ONLY_CHAT_IDS = frozenset({GROUP})

        async def collect_text_messages(event, data):
            return "ignored"

        await self.call(collect_text_messages)
        self.assertEqual(self.store.calls, [])


if __name__ == "__main__":
    unittest.main()

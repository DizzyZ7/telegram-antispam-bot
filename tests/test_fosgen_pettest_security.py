"""Laboratory safety, frozen portraits and concurrent allocation contracts."""
import asyncio
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from aiogram.dispatcher.event.bases import SkipHandler

from fosgen_pets.core import Appearance
from fosgen_pets import pettest


LEGACY_DNA = {
    "species": "kitten", "palette": "cream", "pattern": "solid",
    "eyes": "brown", "marking": "none", "marking_variant": 0,
}


def seed_legacy_database(path: Path, count: int = 1) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE fosgen_pettest (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER NOT NULL, dna TEXT NOT NULL,
            fingerprint TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        for index in range(count):
            conn.execute(
                "INSERT INTO fosgen_pettest(owner_id,dna,fingerprint) VALUES(?,?,?)",
                (123, json.dumps(LEGACY_DNA), f"legacy-{index}"),
            )


class PettestGrammarTests(unittest.TestCase):
    def test_rejects_non_commands_control_characters_and_integer_overflow(self):
        for text in (
            "", "anything", "/notpettest", "/pettest\n1 delete", "/pettest\x001",
            "/pettest 9223372036854775808", "/pettest １２", "/pettest ١",
            "/pettest " + "9" * 10000, "/pettest 1 delete trailing",
        ):
            with self.subTest(text=text[:50]), self.assertRaises(ValueError):
                pettest.parse_command(text)

    def test_admin_ids_have_the_same_safe_ascii_integer_range_as_sqlite(self):
        for text in ("9223372036854775808", "１２３", "١٢٣", "9" * 10000):
            with self.subTest(text=text[:50]), self.assertRaises(ValueError):
                pettest.parse_admin_ids(text)

    def test_species_dna_and_bounded_seeded_series_grammar(self):
        self.assertTrue(callable(getattr(pettest, "parse_request", None)))
        request = pettest.parse_request("/pettest@FosgenBot kitten")
        self.assertEqual((request.operation, request.species), ("create", "kitten"))
        for text in ("/pettest 12 dna", "/pettest dna 12"):
            request = pettest.parse_request(text)
            self.assertEqual((request.operation, request.ident), ("dna", 12))
        request = pettest.parse_request("/pettest series otter 8 999999")
        self.assertEqual(
            (request.operation, request.species, request.count, request.pattern_seed),
            ("series", "otter", 8, 999999),
        )
        for text in (
            "/pettest series kitten 1", "/pettest series kitten 9",
            "/pettest series kitten 2 1000000", "/pettest series kitten 2 -1",
            "/pettest series dragon 2", "/pettest series kitten 2 0 extra",
        ):
            with self.subTest(text=text), self.assertRaises(ValueError):
                pettest.parse_request(text)


class PettestBulkDeleteGrammarTests(unittest.TestCase):
    def test_bulk_delete_rejects_unbounded_or_invalid_input(self):
        for command in (
            "/pettest id -1 delete", "/pettest id 0-5 delete",
            "/pettest id 5-1 delete", "/pettest id 1-1000000 delete",
            "/pettest id 1-31 delete", "/pettest id 1--3 delete",
            "/pettest id 1,,3 delete", "/pettest 1,3, delete",
            "/pettest all", "/pettest all dna", "/pettest id 1-27",
            "/pettest id 1-27 wipe", "/pettest id 9223372036854775808 delete",
            "/pettest 1,2,3;DROP delete", "/pettest 1-２ delete",
        ):
            with self.subTest(command=command), self.assertRaises(ValueError):
                pettest.parse_request(command)

    def test_bulk_deduplication_and_bot_mention(self):
        request = pettest.parse_request("/pettest@FosgenBot id 1,2,2,4-6 delete")
        self.assertEqual(request.identifiers, (1, 2, 4, 5, 6))
        self.assertEqual(request.operation, "delete_many")


class PettestStorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "fosgen_pettest.sqlite3"

    async def test_bulk_delete_is_owner_scoped_idempotent_and_does_not_touch_permanent_pets(self):
        seed_legacy_database(self.path, count=5)
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                "INSERT INTO fosgen_pettest(owner_id,dna,fingerprint) VALUES (?,?,?)",
                (456, json.dumps(LEGACY_DNA), "other-owner"),
            )
        store = pettest.TestPetStore(self.path)
        self.assertEqual(await store.delete_many(123, (1, 3, 5, 6, 500)), [1, 3, 5])
        self.assertEqual([id for id, _ in await store.list(123)], [4, 2])
        self.assertEqual([id for id, _ in await store.list(456)], [6])
        self.assertEqual(await store.delete_many(123, (1, 3, 5, 6)), [])
        self.assertEqual(await store.delete_many(123, None), [2, 4])
        self.assertEqual(await store.delete_many(123, None), [])
        self.assertEqual([id for id, _ in await store.list(456)], [6])

    async def test_deleted_ids_release_capacity_without_resurrecting_stale_telegram_ids(self):
        seed_legacy_database(self.path, count=30)
        store = pettest.TestPetStore(self.path)
        self.assertEqual(len(await store.list(123)), 30)
        with self.assertRaises(ValueError):
            await store.create(123, "kitten")
        self.assertEqual(await store.delete_many(123, (1, 2, 3, 99)), [1, 2, 3])
        self.assertIsNone(await store.get(1, 123))
        self.assertEqual(len(await store.list(123)), 27)
        new_pets = await store.create_series(123, "kitten", 3, pattern_seed=123456)
        self.assertEqual(len(new_pets), 3)
        # Reused numbers would allow old /pettest ID delete messages to affect new pets.
        self.assertTrue(all(pet.id > 30 for pet in new_pets))
        self.assertEqual(len(await store.list(123)), 30)
        with self.assertRaises(ValueError):
            await store.create(123, "kitten")
        self.assertEqual(len(await store.delete_many(123, None)), 30)
        self.assertEqual(await store.list(123), [])
        self.assertIsNone(await store.get(3, 123))
        self.assertEqual(len(await store.create_series(123, "kitten", 2, pattern_seed=123456)), 2)
        self.assertEqual(len(await store.list(123)), 2)

    async def test_bulk_delete_rolls_back_if_sqlite_rejects_any_row(self):
        seed_legacy_database(self.path, count=3)
        with sqlite3.connect(self.path) as conn:
            conn.execute("""CREATE TRIGGER deny_test_delete BEFORE DELETE ON fosgen_pettest
                WHEN OLD.id=2 BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END""")
        store = pettest.TestPetStore(self.path)
        with self.assertRaises(sqlite3.IntegrityError):
            await store.delete_many(123, (1, 2))
        self.assertEqual([ident for ident, _ in await store.list(123)], [3, 2, 1])

    async def test_bulk_delete_rejects_invalid_identifiers_before_sql(self):
        store = pettest.TestPetStore(self.path)
        for ids in ((0,), (True,), (2**63,), tuple(range(1, 32)), ()):
            with self.subTest(ids=str(ids)), self.assertRaises(ValueError):
                await store.delete_many(123, ids)
        with self.assertRaises(ValueError):
            await store.delete_many(0, None)

    async def test_cached_portrait_survives_store_reload_and_renderer_upgrade(self):
        store = pettest.TestPetStore(self.path)
        first = await store.create(123)
        self.assertTrue(getattr(first, "png", b"").startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertIn("<svg", first.svg)
        with patch.object(pettest, "render_portrait", side_effect=AssertionError("upgraded renderer"), create=True):
            second = await pettest.TestPetStore(self.path).get(first.id, 123)
        self.assertEqual((second.png, second.svg, second.pixel_hash), (first.png, first.svg, first.pixel_hash))

    async def test_legacy_record_is_rendered_as_v1_and_cached_on_first_show(self):
        seed_legacy_database(self.path)
        original = Appearance(**LEGACY_DNA, asset_version=1)
        expected_png = await asyncio.to_thread(pettest.render_png, original)
        first = await pettest.TestPetStore(self.path).get(1, 123)
        self.assertEqual(first.appearance.asset_version, 1)
        self.assertEqual(getattr(first, "png", None), expected_png)
        with patch.object(pettest, "render_portrait", side_effect=AssertionError("no rerender"), create=True):
            second = await pettest.TestPetStore(self.path).get(1, 123)
        self.assertEqual(second.png, expected_png)
        with sqlite3.connect(self.path) as conn:
            self.assertEqual(json.loads(conn.execute("SELECT dna FROM fosgen_pettest WHERE id=1").fetchone()[0]), LEGACY_DNA)

    async def test_concurrent_store_instances_cannot_exceed_owner_limit(self):
        seed_legacy_database(self.path, 29)
        first_count = threading.Barrier(8)
        real_connect = sqlite3.connect

        class SimultaneousReaders(sqlite3.Connection):
            counted = False

            def execute(self, sql, parameters=()):
                cursor = super().execute(sql, parameters)
                if sql.startswith("SELECT COUNT(*) FROM fosgen_pettest") and not self.counted:
                    self.counted = True
                    # Every store sees 29 before any can try inserting. The
                    # second capacity check must happen under the write lock.
                    row = cursor.fetchone()
                    cursor.close()
                    first_count.wait(timeout=10)
                    return SimpleNamespace(fetchone=lambda: row)
                return cursor

        def connect(*args, **kwargs):
            return real_connect(*args, **kwargs, factory=SimultaneousReaders)

        with patch.object(pettest.sqlite3, "connect", side_effect=connect):
            results = await asyncio.gather(
                *(pettest.TestPetStore(self.path).create(123) for _ in range(8)),
                return_exceptions=True,
            )
        self.assertEqual(sum(not isinstance(result, Exception) for result in results), 1)
        self.assertTrue(all(isinstance(result, ValueError) for result in results if isinstance(result, Exception)), results)
        self.assertEqual(len(await pettest.TestPetStore(self.path).list(123)), 30)

    async def test_every_operation_closes_its_sqlite_connection(self):
        opened = []
        real_connect = sqlite3.connect

        class TrackedConnection(sqlite3.Connection):
            was_closed = False

            def close(self):
                self.was_closed = True
                return super().close()

        def connect(*args, **kwargs):
            conn = real_connect(*args, **kwargs, factory=TrackedConnection)
            opened.append(conn)
            return conn

        with patch.object(pettest.sqlite3, "connect", side_effect=connect):
            store = pettest.TestPetStore(self.path)
            pet = await store.create(123)
            await store.get(pet.id, 123)
            await store.list(123)
            await store.delete(pet.id, 123)
        self.assertTrue(opened)
        self.assertTrue(all(conn.was_closed for conn in opened))

    async def test_seeded_series_is_all_or_nothing_at_the_owner_limit(self):
        seed_legacy_database(self.path, 29)
        store = pettest.TestPetStore(self.path)
        self.assertTrue(callable(getattr(store, "create_series", None)))
        with self.assertRaises(ValueError):
            await store.create_series(123, "kitten", 2, pattern_seed=0)
        self.assertEqual(len(await store.list(123)), 29)

    async def test_seeded_series_varies_only_pattern_seed_from_one_base_appearance(self):
        store = pettest.TestPetStore(self.path)
        self.assertTrue(callable(getattr(store, "create_series", None)))
        pets = await store.create_series(123, "otter", 3, pattern_seed=42)
        self.assertEqual(len(pets), 3)
        self.assertEqual({pet.appearance.species for pet in pets}, {"otter"})
        self.assertEqual([pet.appearance.pattern_seed for pet in pets], [42, 43, 44])
        base_fields = dict(pets[0].appearance.__dict__)
        base_fields.pop("pattern_seed")
        for pet in pets[1:]:
            fields = dict(pet.appearance.__dict__)
            fields.pop("pattern_seed")
            self.assertEqual(fields, base_fields)
        self.assertEqual(len({pet.id for pet in pets}), 3)
        self.assertEqual(len({pet.pixel_hash for pet in pets}), 3)
        self.assertEqual(len(await store.list(123)), 3)

    async def test_seeded_comparison_wraps_at_the_pattern_seed_boundary(self):
        pets = await pettest.TestPetStore(self.path).create_series(123, "kitten", 2, pattern_seed=999999)
        self.assertEqual([pet.appearance.pattern_seed for pet in pets], [999999, 0])
        self.assertNotEqual(pets[0].png, pets[1].png)

    async def test_raster_duplicate_is_rejected_even_when_dna_differs(self):
        png = b"same raster"
        portrait = SimpleNamespace(svg="<svg/>", png=png, pixel_hash=hashlib.sha256(png).hexdigest())
        store = pettest.TestPetStore(self.path)
        with patch.object(pettest, "render_portrait", return_value=portrait, create=True):
            await store.create(123)
            with self.assertRaises(RuntimeError):
                await store.create(123)
        self.assertEqual(len(await store.list(123)), 1)

    async def test_render_failure_does_not_create_a_record_or_partial_series(self):
        store = pettest.TestPetStore(self.path)
        with patch.object(pettest, "render_portrait", side_effect=RuntimeError("rasterizer unavailable"), create=True):
            with self.assertRaises(RuntimeError):
                await store.create(123)
        self.assertEqual(await store.list(123), [])
        self.assertTrue(callable(getattr(store, "create_series", None)))
        with patch.object(pettest, "render_portrait", side_effect=[
            SimpleNamespace(svg="<svg/>", png=b"one", pixel_hash="a" * 64),
            RuntimeError("second raster failed"),
        ], create=True):
            with self.assertRaises(RuntimeError):
                await store.create_series(123, "kitten", 2)
        self.assertEqual(await store.list(123), [])

    async def test_deleting_all_test_pets_does_not_change_production_database(self):
        production = self.path.with_name("production.sqlite3")
        with sqlite3.connect(production) as conn:
            conn.execute("CREATE TABLE fosgen_pets(id INTEGER PRIMARY KEY, owner_id INTEGER)")
            conn.execute("INSERT INTO fosgen_pets VALUES(1,123)")
        store = pettest.TestPetStore(self.path)
        pet = await store.create(123)
        self.assertTrue(await store.delete(pet.id, 123))
        self.assertEqual(await store.list(123), [])
        with sqlite3.connect(production) as conn:
            self.assertEqual(conn.execute("SELECT * FROM fosgen_pets").fetchall(), [(1, 123)])

    async def test_invalid_identifiers_are_rejected_before_sqlite_conversion(self):
        store = pettest.TestPetStore(self.path)
        for ident in (0, -1, 2**63, True):
            with self.subTest(ident=ident), self.assertRaises(ValueError):
                await store.get(ident, 123)
        with self.assertRaises(ValueError):
            await store.create(2**63)


class FakeObserver:
    def __init__(self):
        self.handlers = []

    def register(self, callback, *filters):
        self.handlers.append(SimpleNamespace(callback=callback))


class FakeMessage:
    def __init__(self, text, owner=123, *, bot=False, chat_type="private"):
        self.text = text
        self.from_user = SimpleNamespace(id=owner, is_bot=bot)
        self.chat = SimpleNamespace(id=owner, type=chat_type)
        self.answers = []
        self.photos = []
        self.albums = []

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))

    async def answer_photo(self, photo, **kwargs):
        self.photos.append((photo, kwargs))

    async def answer_media_group(self, media, **kwargs):
        self.albums.append(media)


class PettestHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.observer = FakeObserver()
        app = SimpleNamespace(dp=SimpleNamespace(message=self.observer))
        pettest.register_pettest_handlers(app, admin_ids=frozenset({123}), data_dir=Path(self.folder.name))
        self.handler = self.observer.handlers[0].callback

    async def test_unauthorized_and_bot_senders_continue_to_moderation(self):
        for message in (FakeMessage("/pettest", owner=456), FakeMessage("/pettest", bot=True)):
            with self.assertRaises(SkipHandler):
                await self.handler(message)
            self.assertEqual(message.answers, [])
            self.assertEqual(message.photos, [])

    async def test_group_command_does_not_create_temporary_pets(self):
        message = FakeMessage("/pettest", chat_type="group")
        await self.handler(message)
        self.assertEqual(len(message.answers), 1)
        self.assertEqual(message.photos, [])
        self.assertEqual(await pettest.TestPetStore(Path(self.folder.name) / "fosgen_pettest.sqlite3").list(123), [])

    async def test_bulk_delete_handler_accepts_exact_user_syntax(self):
        path = Path(self.folder.name) / "fosgen_pettest.sqlite3"
        seed_legacy_database(path, count=27)
        message = FakeMessage("/pettest id 1-27 delete")
        await self.handler(message)
        self.assertIn("Удалено тестовых питомцев: 27", message.answers[0][0])
        self.assertEqual(await pettest.TestPetStore(path).list(123), [])
        await self.handler(FakeMessage("/pettest list"))
        self.assertEqual(len(message.photos), 0)

    async def test_list_shows_only_active_capacity_after_bulk_delete(self):
        path = Path(self.folder.name) / "fosgen_pettest.sqlite3"
        seed_legacy_database(path, count=27)
        before = FakeMessage("/pettest list")
        await self.handler(before)
        self.assertIn("занято 27/30", before.answers[0][0])
        await self.handler(FakeMessage("/pettest id 1-27 delete"))
        after = FakeMessage("/pettest list")
        await self.handler(after)
        self.assertIn("занято 0/30", after.answers[0][0])
        self.assertIn("свободно 30", after.answers[0][0])

    async def test_bulk_delete_handler_keeps_other_owners_data(self):
        path = Path(self.folder.name) / "fosgen_pettest.sqlite3"
        seed_legacy_database(path, count=3)
        with sqlite3.connect(path) as conn:
            conn.execute(
                "INSERT INTO fosgen_pettest(owner_id,dna,fingerprint) VALUES(?,?,?)",
                (456, json.dumps(LEGACY_DNA), "other-owner"),
            )
        message = FakeMessage("/pettest all delete")
        await self.handler(message)
        self.assertIn("Удалено тестовых питомцев: 3", message.answers[0][0])
        self.assertEqual(await pettest.TestPetStore(path).list(123), [])
        self.assertEqual(len(await pettest.TestPetStore(path).list(456)), 1)

    async def test_dna_reply_is_complete_and_html_escaped(self):
        store = pettest.TestPetStore(Path(self.folder.name) / "fosgen_pettest.sqlite3")
        pet = await store.create(123)
        dangerous = replace(pet.appearance, palette="<coat&color>")
        with sqlite3.connect(store.path) as conn:
            conn.execute("UPDATE fosgen_pettest SET dna=? WHERE id=?", (dangerous.canonical_dna(), pet.id))
        message = FakeMessage(f"/pettest {pet.id} dna")
        await self.handler(message)
        self.assertEqual(len(message.answers), 1)
        self.assertIn("&lt;coat&amp;color&gt;", message.answers[0][0])
        self.assertNotIn("<coat&color>", message.answers[0][0])
        self.assertIn('"marking_variant":', message.answers[0][0])
        self.assertEqual(message.answers[0][1].get("parse_mode"), "HTML")

    async def test_series_sends_album_with_ids_and_pattern_seed(self):
        message = FakeMessage("/pettest series kitten 2 42")
        await self.handler(message)
        self.assertEqual(len(message.albums), 1)
        self.assertEqual(len(message.albums[0]), 2)
        for offset, photo in enumerate(message.albums[0]):
            self.assertRegex(photo.caption, r"#\d+")
            self.assertIn(f"Pattern Seed: {42 + offset}", photo.caption)
        self.assertEqual(len(await pettest.TestPetStore(Path(self.folder.name) / "fosgen_pettest.sqlite3").list(123)), 2)

    async def test_parallel_commands_have_a_bounded_render_queue(self):
        started = threading.Event()
        release = threading.Event()
        real_render = pettest.render_portrait

        def slow_render(appearance):
            started.set()
            if not release.wait(timeout=5):
                raise RuntimeError("test render was not released")
            return real_render(appearance)

        first = FakeMessage("/pettest kitten")
        second = FakeMessage("/pettest kitten")
        third = FakeMessage("/pettest kitten")
        tasks = []
        with patch.object(pettest, "render_portrait", side_effect=slow_render):
            try:
                tasks.append(asyncio.create_task(self.handler(first)))
                self.assertTrue(await asyncio.to_thread(started.wait, 2))
                tasks.append(asyncio.create_task(self.handler(second)))
                await asyncio.sleep(0)
                await self.handler(third)
                self.assertEqual(third.photos, [])
                self.assertEqual(len(third.answers), 1)
                self.assertIn("занята", third.answers[0][0])
            finally:
                release.set()
                await asyncio.gather(*tasks)
        self.assertEqual(len(first.photos), 1)
        self.assertEqual(len(second.photos), 1)


if __name__ == "__main__":
    unittest.main()

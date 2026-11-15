"""Versioned private laboratory grammar, atomic allocation and Telegram output."""
from __future__ import annotations

import asyncio
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from aiogram import Bot, Dispatcher, F
from aiogram.client.session.base import BaseSession
from aiogram.methods import GetMe, SendMediaGroup, SendMessage, SendPhoto
from aiogram.types import Chat, Message, Update, User

from fosgen_pets import pettest
from fosgen_pets.core import Appearance, MARKING_VARIANT_COUNTS, build_appearance, validate_appearance


OWNER = 123
NOW = datetime(2026, 11, 15, tzinfo=timezone.utc)


def seed_capacity(path: Path, count: int) -> None:
    """Use saved historical rows so capacity checks do not need 30 renders."""
    store = pettest.TestPetStore(path)
    with closing(store._connect()) as conn:
        appearance = build_appearance("kitten", "capacity-fixture")
        for index in range(count):
            conn.execute(
                "INSERT INTO fosgen_pettest(owner_id,dna,fingerprint,png,svg,pixel_hash) "
                "VALUES(?,?,?,?,?,?)",
                (OWNER, appearance.canonical_dna(), f"capacity-{index}", b"saved", "<svg/>", f"saved-{index}"),
            )


class VersionedGrammarTests(unittest.TestCase):
    def test_legacy_generation_stays_v2(self):
        for command in ("/pettest", "/pettest kitten", "/pettest series otter 2 42"):
            with self.subTest(command=command):
                request = pettest.parse_request(command)
                self.assertEqual(getattr(request, "asset_version", None), 2)
                self.assertIsNone(getattr(request, "marking", "missing"))

    def test_version_prefix_selects_random_species_single_pet_or_series(self):
        cases = (
            ("/pettest v3", "create", None, 1, None, 3),
            ("/pettest@FosgenBot v2 kitten", "create", "kitten", 1, None, 2),
            ("/pettest v3 otter", "create", "otter", 1, None, 3),
            ("/pettest v3 series calf 8 999999", "series", "calf", 8, 999999, 3),
            ("/pettest v2 series bunny 2", "series", "bunny", 2, None, 2),
        )
        for command, operation, species, count, seed, version in cases:
            with self.subTest(command=command):
                request = pettest.parse_request(command)
                self.assertEqual(
                    (request.operation, request.species, request.count, request.pattern_seed, request.asset_version),
                    (operation, species, count, seed, version),
                )

    def test_rare_and_compare_have_explicit_bounded_grammar(self):
        for version in (2, 3):
            for marking in ("star", "heart", "blaze", "freckle", "sock_left", "sock_right"):
                request = pettest.parse_request(f"/pettest v{version} rare kitten {marking} 0")
                self.assertEqual(
                    (request.operation, request.species, request.marking, request.pattern_seed, request.asset_version),
                    ("create", "kitten", marking, 0, version),
                )
        unseeded = pettest.parse_request("/pettest v3 rare alpaca heart")
        self.assertIsNone(unseeded.pattern_seed)
        request = pettest.parse_request("/pettest compare otter 999999")
        self.assertEqual((request.operation, request.species, request.pattern_seed), ("compare", "otter", 999999))
        self.assertEqual(pettest.parse_request("/pettest compare kitten").operation, "compare")

    def test_versions_cannot_be_used_to_escape_old_read_and_delete_grammar(self):
        for command in (
            "/pettest v1", "/pettest v4 kitten", "/pettest v3 dragon",
            "/pettest v3 list", "/pettest v3 1", "/pettest v3 all delete",
            "/pettest v3 compare kitten", "/pettest compare dragon",
            "/pettest compare kitten -1", "/pettest compare kitten 1000000",
            "/pettest compare kitten 0 extra", "/pettest v3 series kitten 1",
            "/pettest v3 rare kitten none", "/pettest v3 rare kitten <heart>",
            "/pettest v3 rare kitten heart 1000000", "/pettest v3 rare kitten heart -1",
            "/pettest v3 rare kitten star 0 extra", "/pettest v3\nkitten",
            "/pettest v3 rare dragon star", "/pettest v3 kitten 42",
        ):
            with self.subTest(command=command), self.assertRaises(ValueError):
                pettest.parse_request(command)


class VersionedStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "fosgen_pettest.sqlite3"
        self.store = pettest.TestPetStore(self.path)

    async def test_v3_png_and_dna_are_frozen_across_restart(self):
        pet = await self.store.create(OWNER, "kitten", 42, asset_version=3)
        self.assertEqual((pet.appearance.asset_version, pet.appearance.pattern_seed), (3, 42))
        self.assertTrue(pet.png.startswith(b"\x89PNG\r\n\x1a\n"))
        validate_appearance(pet.appearance)
        with patch.object(pettest, "render_portrait", side_effect=AssertionError("cached art must be used")):
            restored = await pettest.TestPetStore(self.path).get(pet.id, OWNER)
        self.assertEqual(restored, pet)
        self.assertIsNone(await self.store.get(pet.id, 456))

    async def test_new_allocation_accepts_only_v2_v3_finite_species_seed_and_rare_marking(self):
        for options in (
            {"asset_version": 1}, {"asset_version": 4}, {"asset_version": True},
            {"marking": "none"}, {"marking": "unknown"}, {"marking": []},
            {"pattern_seed": -1}, {"pattern_seed": 1000000}, {"pattern_seed": True},
            {"species": "dragon"}, {"species": []},
        ):
            with self.subTest(options=options), self.assertRaises(ValueError):
                await self.store.create(OWNER, **options)
        with self.assertRaises(ValueError):
            await self.store.create_series(OWNER, "kitten", 2, asset_version=1)
        with self.assertRaises(ValueError):
            await self.store.compare(OWNER, "dragon", 42)
        with self.assertRaises(ValueError):
            await self.store.compare(OWNER, "kitten", True)
        self.assertEqual(await self.store.list(OWNER), [])

    async def test_rare_generation_uses_canonical_visible_variants(self):
        for marking in ("star", "heart", "blaze", "freckle", "sock_left", "sock_right"):
            pet = await self.store.create(OWNER, "kitten", 42, asset_version=3, marking=marking)
            self.assertEqual(pet.appearance.marking, marking)
            self.assertLess(pet.appearance.marking_variant, MARKING_VARIANT_COUNTS[marking])
            self.assertGreaterEqual(pet.appearance.marking_variant, 0)
            validate_appearance(pet.appearance)
        self.assertEqual(len(await self.store.list(OWNER)), 6)

    async def test_v3_seeded_series_changes_only_wrapping_pattern_seed(self):
        pets = await self.store.create_series(OWNER, "otter", 3, 999999, asset_version=3, marking="star")
        self.assertEqual([pet.appearance.pattern_seed for pet in pets], [999999, 0, 1])
        self.assertEqual({pet.appearance.asset_version for pet in pets}, {3})
        self.assertEqual({pet.appearance.marking for pet in pets}, {"star"})
        first = json.loads(pets[0].appearance.canonical_dna())
        first.pop("pattern_seed")
        for pet in pets[1:]:
            fields = json.loads(pet.appearance.canonical_dna())
            fields.pop("pattern_seed")
            self.assertEqual(fields, first)
        self.assertEqual(len({pet.pixel_hash for pet in pets}), 3)

    async def test_compare_preserves_every_gene_except_asset_version_and_both_pngs(self):
        pets = await self.store.compare(OWNER, "fox", 42)
        self.assertEqual([pet.appearance.asset_version for pet in pets], [2, 3])
        self.assertEqual(pets[1].appearance, replace(pets[0].appearance, asset_version=3))
        self.assertEqual({pet.appearance.pattern_seed for pet in pets}, {42})
        self.assertNotEqual(pets[0].pixel_hash, pets[1].pixel_hash)
        with patch.object(pettest, "render_portrait", side_effect=AssertionError("no rerender")):
            restored = [await pettest.TestPetStore(self.path).get(pet.id, OWNER) for pet in pets]
        self.assertEqual(restored, pets)

    async def test_compare_limit_is_atomic_and_deleted_ids_release_real_capacity(self):
        seed_capacity(self.path, 29)
        with self.assertRaises(ValueError):
            await self.store.compare(OWNER, "kitten", 42)
        self.assertEqual(len(await self.store.list(OWNER)), 29)
        self.assertTrue(await self.store.delete(1, OWNER))
        pets = await self.store.compare(OWNER, "kitten", 42)
        self.assertEqual(len(await self.store.list(OWNER)), 30)
        self.assertTrue(all(pet.id > 29 for pet in pets))
        self.assertIsNone(await self.store.get(1, OWNER))
        self.assertEqual(await self.store.delete_many(OWNER, None), list(range(2, 32)))
        self.assertEqual(await self.store.list(OWNER), [])

    async def test_v3_failure_does_not_leave_v2_half_of_compare_or_partial_series(self):
        original = pettest.render_portrait

        def fail_v3(appearance):
            if appearance.asset_version == 3:
                raise RuntimeError("v3 master unavailable")
            return original(appearance)

        with patch.object(pettest, "render_portrait", side_effect=fail_v3):
            with self.assertRaisesRegex(RuntimeError, "v3 master"):
                await self.store.compare(OWNER, "kitten", 42)
        self.assertEqual(await self.store.list(OWNER), [])
        with patch.object(pettest, "render_portrait", side_effect=[
            SimpleNamespace(svg="<svg/>", png=b"first", pixel_hash="a" * 64),
            RuntimeError("second portrait unavailable"),
        ]):
            with self.assertRaisesRegex(RuntimeError, "second portrait"):
                await self.store.create_series(OWNER, "kitten", 2, 42, asset_version=3)
        self.assertEqual(await self.store.list(OWNER), [])

    async def test_compare_rolls_back_if_persistence_rejects_the_v3_record(self):
        with closing(self.store._connect()) as conn:
            conn.execute("""CREATE TRIGGER reject_v3 BEFORE INSERT ON fosgen_pettest
                WHEN json_extract(NEW.dna, '$.asset_version')=3
                BEGIN SELECT RAISE(ABORT, 'synthetic v3 storage failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            await self.store.compare(OWNER, "kitten", 42)
        self.assertEqual(await self.store.list(OWNER), [])

    async def test_concurrent_compares_cannot_split_pairs_or_exceed_capacity(self):
        seed_capacity(self.path, 28)
        results = await asyncio.gather(
            *(pettest.TestPetStore(self.path).compare(OWNER, "kitten", 42) for _ in range(3)),
            return_exceptions=True,
        )
        winners = [result for result in results if not isinstance(result, Exception)]
        failures = [result for result in results if isinstance(result, Exception)]
        self.assertEqual(len(winners), 1, results)
        self.assertEqual([pet.appearance.asset_version for pet in winners[0]], [2, 3])
        self.assertTrue(all(isinstance(result, ValueError) for result in failures), failures)
        self.assertEqual(len(await self.store.list(OWNER)), 30)

    async def test_compare_retries_dna_collisions_as_one_pair(self):
        original = pettest.render_portrait
        base = Appearance("kitten", "cream", "solid", "brown", "none", 0, asset_version=2, pattern_seed=42)
        image = await asyncio.to_thread(original, base)
        # Reserve the DNA as a previous legitimate allocation would do.
        with closing(self.store._connect()) as conn:
            conn.execute(
                "INSERT INTO fosgen_pettest(owner_id,dna,fingerprint,png,svg,pixel_hash) VALUES(?,?,?,?,?,?)",
                (456, base.canonical_dna(), base.fingerprint(), image.png, image.svg, image.pixel_hash),
            )
        fresh = replace(base, palette="ginger")
        generated = iter([base, fresh])
        with patch.object(pettest, "build_appearance", side_effect=lambda *args, **kwargs: next(generated)):
            pets = await self.store.compare(OWNER, "kitten", 42)
        self.assertNotEqual(pets[0].appearance.fingerprint(), base.fingerprint())
        self.assertEqual(pets[0].appearance, fresh)
        self.assertEqual(pets[1].appearance, replace(pets[0].appearance, asset_version=3))
        self.assertEqual(len(await self.store.list(OWNER)), 2)

    async def test_compare_retries_when_versions_render_to_the_same_raster(self):
        original = pettest.render_portrait
        base = Appearance("kitten", "cream", "solid", "brown", "none", 0, asset_version=2, pattern_seed=42)
        fresh = replace(base, palette="ginger")
        first_image = None
        calls = 0

        def duplicate_first_pair(appearance):
            nonlocal first_image, calls
            calls += 1
            if calls == 1:
                first_image = original(appearance)
                return first_image
            if calls == 2:
                return first_image
            return original(appearance)

        with (
            patch.object(pettest, "render_portrait", side_effect=duplicate_first_pair),
            patch.object(pettest, "build_appearance", side_effect=[base, fresh]),
        ):
            pets = await self.store.compare(OWNER, "kitten", 42)
        self.assertEqual(pets[0].appearance, fresh)
        self.assertNotEqual(pets[0].pixel_hash, first_image.pixel_hash)
        self.assertNotEqual(pets[0].pixel_hash, pets[1].pixel_hash)
        self.assertEqual(pets[1].appearance, replace(pets[0].appearance, asset_version=3))
        self.assertEqual(len(await self.store.list(OWNER)), 2)


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
            return User(id=777, is_bot=True, first_name="Fosgen", username="FosgenBot")
        if isinstance(method, (SendMessage, SendPhoto)):
            return Message(message_id=901, date=NOW, chat=Chat(id=int(method.chat_id), type="private"))
        if isinstance(method, SendMediaGroup):
            return [Message(message_id=901 + offset, date=NOW, chat=Chat(id=int(method.chat_id), type="private"))
                    for offset, _ in enumerate(method.media)]
        raise AssertionError(f"Unexpected Telegram operation {type(method).__name__}")


class VersionedDispatcherTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "fosgen_pettest.sqlite3"
        self.session = RecordingSession()
        self.bot = Bot("777:" + "a" * 35, session=self.session)
        self.addAsyncCleanup(self.bot.session.close)
        self.app = SimpleNamespace(dp=Dispatcher())
        self.forwarded = []

        async def legacy(message):
            self.forwarded.append(message.text)

        self.app.dp.message.register(legacy, F.text)
        pettest.register_pettest_handlers(self.app, admin_ids=frozenset({OWNER}), data_dir=Path(self.folder.name))
        self.update_id = 0

    async def dispatch(self, text, *, owner=OWNER, chat=OWNER, bot=False):
        self.update_id += 1
        event = Message(
            message_id=self.update_id, date=NOW,
            chat=Chat(id=chat, type="private" if chat > 0 else "supergroup"),
            from_user=User(id=owner, is_bot=bot, first_name="User"), text=text,
        )
        return await self.app.dp.feed_update(self.bot, Update(update_id=self.update_id, message=event))

    async def test_compare_album_has_distinct_v2_v3_names_and_matching_dna(self):
        await self.dispatch("/pettest compare kitten 42")
        albums = [call for call in self.session.requests if isinstance(call, SendMediaGroup)]
        self.assertEqual(len(albums), 1)
        self.assertEqual(len(albums[0].media), 2)
        for version, item in zip((2, 3), albums[0].media):
            self.assertIn(f"v{version}", item.media.filename)
            self.assertIn(f"v{version}", item.caption)
            self.assertIn("Pattern Seed: 42", item.caption)
        store = pettest.TestPetStore(self.path)
        records = [await store.get(ident, OWNER) for ident, _ in await store.list(OWNER)]
        self.assertEqual({pet.appearance.asset_version for pet in records}, {2, 3})
        self.assertEqual(self.forwarded, [])

    async def test_v3_rare_routes_and_default_command_still_creates_v2(self):
        with patch.object(pettest.secrets, "token_hex", return_value="handler-rare"):
            await self.dispatch("/pettest v3 rare otter heart 42")
            await self.dispatch("/pettest kitten")
        photos = [call for call in self.session.requests if isinstance(call, SendPhoto)]
        self.assertEqual(len(photos), 2)
        self.assertIn("v3", photos[0].photo.filename)
        self.assertIn("heart", photos[0].caption)
        self.assertIn("v2", photos[1].photo.filename)
        self.assertEqual(len(await pettest.TestPetStore(self.path).list(OWNER)), 2)

    async def test_versioned_series_dispatch_keeps_one_v3_gallery_with_wrapping_seeds(self):
        await self.dispatch("/pettest v3 series otter 3 999999")
        albums = [call for call in self.session.requests if isinstance(call, SendMediaGroup)]
        self.assertEqual(len(albums), 1)
        self.assertEqual(len(albums[0].media), 3)
        for seed, item in zip((999999, 0, 1), albums[0].media):
            self.assertIn("v3", item.media.filename)
            self.assertIn(f"Pattern Seed: {seed}", item.caption)
        self.assertEqual(len(await pettest.TestPetStore(self.path).list(OWNER)), 3)

    async def test_versioned_commands_keep_admin_private_and_bot_guards(self):
        await self.dispatch("/pettest v3 kitten", owner=456, chat=456)
        await self.dispatch("/pettest compare kitten", bot=True)
        await self.dispatch("/pettest v3 rare kitten star", chat=-100123)
        await self.dispatch("/pettest compare kitten", chat=999)
        self.assertEqual(len(self.forwarded), 3)
        self.assertEqual([call for call in self.session.requests if isinstance(call, (SendPhoto, SendMediaGroup))], [])
        self.assertEqual(await pettest.TestPetStore(self.path).list(OWNER), [])


if __name__ == "__main__":
    unittest.main()

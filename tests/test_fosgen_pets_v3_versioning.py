"""Version 3 is explicit; released DNA and production issuance stay frozen."""
from dataclasses import replace
from hashlib import sha256
from io import BytesIO
import subprocess
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from PIL import Image

from fosgen_pets import core
from fosgen_pets.core import Appearance, build_appearance, validate_appearance


class V3AppearanceTests(unittest.TestCase):
    def test_default_and_historical_generation_keep_exact_released_dna(self):
        fixtures = (
            (1, "kitten", "7d763fc41c6fdc8a821234c91efbe08e0c31d4f43e04bde3b11731a3892d964c"),
            (1, "foal", "64455856082417f5bf840ba2db9bc8311266249e246b349c055eb84ae6378052"),
            (1, "calf", "fab43e68f90721ec48410b462dc9c3cd791c42c369ae91e1cb0f9cc3bc3a8f23"),
            (2, "kitten", "2ca226330bb7bad1b646fdb0508df3e21a726842731356f500d563dc0e2a997e"),
            (2, "foal", "5951648f82f441b4b1367cfb38aec7c855f41b852675e3dda8709ba12e04761d"),
            (2, "calf", "3c4c72eda67da1fde61d0181908140ef330fc4a89b62b2ddb6088b5f57c53b47"),
        )
        for version, species, fingerprint in fixtures:
            with self.subTest(version=version, species=species):
                self.assertEqual(build_appearance(species, "owner:123", asset_version=version).fingerprint(), fingerprint)
        self.assertEqual(build_appearance("kitten", "owner:123").asset_version, 2)

    def test_v3_has_a_fixed_recipe_at_both_pattern_seed_boundaries(self):
        for seed in (0, 999999):
            with self.subTest(pattern_seed=seed):
                appearance = build_appearance("kitten", "versioned-owner", asset_version=3, pattern_seed=seed)
                self.assertEqual(appearance.canonical_dna(),
                    '{"asset_version":3,"eyes":"hazel","marking":"none","marking_variant":0,'
                    '"palette":"black","pattern":"bicolor","pattern_seed":' + str(seed) + ',"species":"kitten"}')
                validate_appearance(appearance)
        self.assertEqual(build_appearance("kitten", "versioned-owner", asset_version=3).pattern_seed, 925719)

    def test_v3_generated_rarity_and_variant_choices_are_frozen(self):
        fixtures = (("0", "none", 0), ("5", "star", 28), ("8", "sock_right", 6),
                    ("9", "freckle", 3), ("24", "sock_left", 0),
                    ("66", "blaze", 18), ("119", "heart", 15))
        for seed, marking, variant in fixtures:
            with self.subTest(seed=seed):
                appearance = build_appearance("kitten", seed, asset_version=3)
                self.assertEqual((appearance.marking, appearance.marking_variant), (marking, variant))

    def test_v3_generation_does_not_follow_later_shared_catalog_extensions(self):
        before = build_appearance("kitten", "5", asset_version=3)
        with patch.object(core, "SPECIES", ("future",)), \
                patch.dict(core.PALETTES, {"kitten": ("future",)}), \
                patch.dict(core.PATTERNS, {"kitten": ("future",)}), \
                patch.object(core, "EYE_COLORS", ("future",)), \
                patch.object(core, "MARKINGS", ()), \
                patch.object(core, "MAX_PATTERN_SEED", 1), \
                patch.dict(core.MARKING_VARIANT_COUNTS, {"star": 1}):
            self.assertEqual(build_appearance("kitten", "5", asset_version=3), before)
            validate_appearance(before)

    def test_v3_validates_every_species_and_rejects_noncanonical_variants(self):
        for species in core.SPECIES:
            appearance = build_appearance(species, "all-species", asset_version=3)
            validate_appearance(appearance)
            for marking, first_invalid in (("none", 1), ("star", 35), ("heart", 35),
                                           ("blaze", 35), ("freckle", 4),
                                           ("sock_left", 9), ("sock_right", 9)):
                with self.subTest(species=species, marking=marking), self.assertRaises(ValueError):
                    validate_appearance(replace(appearance, marking=marking, marking_variant=first_invalid))

    def test_invalid_asset_versions_and_pattern_seed_types_are_rejected(self):
        appearance = Appearance("kitten", "cream", "solid", "amber", "none", 0, 3, 0)
        for version in (True, False, 0, 4, 3.0, "3", None):
            with self.subTest(version=version), self.assertRaises(ValueError):
                build_appearance("kitten", "owner", asset_version=version)
            with self.subTest(version=version), self.assertRaises(ValueError):
                validate_appearance(replace(appearance, asset_version=version))
        for seed in (-1, 1000000, True, False, 0.0, "0"):
            with self.subTest(seed=seed), self.assertRaises(ValueError):
                build_appearance("kitten", "owner", asset_version=3, pattern_seed=seed)
            with self.subTest(seed=seed), self.assertRaises(ValueError):
                validate_appearance(replace(appearance, pattern_seed=seed))


class V3RenderDispatchTests(unittest.TestCase):
    def test_v3_portrait_uses_direct_png_and_rgba_hash_without_cairo(self):
        from fosgen_pets.portrait import render_portrait
        appearance = Appearance("kitten", "cream", "solid", "amber", "none", 0, 3, 0)
        output = BytesIO()
        Image.new("RGBA", (512, 512), (17, 33, 65, 255)).save(output, format="PNG")
        png = output.getvalue()
        svg = '<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512"/>'
        renderer = ModuleType("fosgen_pets.graphics_v3")
        def render_v3(candidate):
            self.assertEqual(candidate, appearance)
            return svg, png
        renderer.render_v3 = render_v3
        with patch.dict(sys.modules, {"fosgen_pets.graphics_v3": renderer}), \
                patch("cairosvg.svg2png", side_effect=AssertionError("v3 must keep the direct PNG")):
            portrait = render_portrait(appearance)
        self.assertEqual((portrait.svg, portrait.png), (svg, png))
        self.assertEqual(portrait.pixel_hash, sha256(bytes((17, 33, 65, 255)) * (512 * 512)).hexdigest())
        self.assertEqual(portrait.renderer_version, "fosgen-plush-v3")

    def test_v3_svg_dispatch_uses_the_standalone_svg_from_the_v3_renderer(self):
        from fosgen_pets.render_svg import render_svg
        appearance = Appearance("kitten", "cream", "solid", "amber", "none", 0, 3, 0)
        renderer = ModuleType("fosgen_pets.graphics_v3")
        renderer.render_v3 = lambda candidate: ('<svg xmlns="http://www.w3.org/2000/svg"/>', b"png")
        with patch.dict(sys.modules, {"fosgen_pets.graphics_v3": renderer}):
            self.assertEqual(render_svg(appearance), '<svg xmlns="http://www.w3.org/2000/svg"/>')

    def test_importing_disabled_pets_does_not_import_v3_art(self):
        process = subprocess.run([sys.executable, "-c",
            "import sys; import fosgen_pets.core, fosgen_pets.portrait, fosgen_pets.runtime; "
            "assert 'fosgen_pets.graphics_v3' not in sys.modules"], capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stderr)


class V3ProductionConfigTests(unittest.TestCase):
    def env(self, **overrides):
        return {"FOSGEN_PETS_ENABLED": "1", "FOSGEN_PETS_CHAT_IDS": "-100", **overrides}

    def test_default_production_issuance_stays_v2(self):
        from fosgen_pets.runtime import PetsConfig
        config = PetsConfig.from_env(self.env())
        self.assertEqual(config.new_asset_version, 2)
        self.assertFalse(config.v3_art_approved)
        approved_only = PetsConfig.from_env(self.env(FOSGEN_PETS_V3_ART_APPROVED="1"))
        self.assertEqual(approved_only.new_asset_version, 2)

    def test_v3_production_issuance_requires_both_explicit_flags(self):
        from fosgen_pets.runtime import PetsConfig
        config = PetsConfig.from_env(self.env(FOSGEN_PETS_NEW_ASSET_VERSION="3", FOSGEN_PETS_V3_ART_APPROVED="1"))
        self.assertEqual(config.new_asset_version, 3)
        self.assertTrue(config.v3_art_approved)
        for approval in (None, "", "0", "false", "true", "yes", "2", " 1"):
            env = self.env(FOSGEN_PETS_NEW_ASSET_VERSION="3")
            if approval is not None:
                env["FOSGEN_PETS_V3_ART_APPROVED"] = approval
            with self.subTest(approval=approval), self.assertRaises(ValueError):
                PetsConfig.from_env(env)

    def test_invalid_or_legacy_production_issuance_version_is_rejected(self):
        from fosgen_pets.runtime import PetsConfig
        for version in ("", "0", "1", "4", "true", "3.0", " 3"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                PetsConfig.from_env(self.env(FOSGEN_PETS_NEW_ASSET_VERSION=version, FOSGEN_PETS_V3_ART_APPROVED="1"))


class V3ProductionRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_unapproved_v3_disables_only_pets_before_storage_is_opened(self):
        from fosgen_pets.runtime import start_pet_runtime
        app = SimpleNamespace(ALLOWED_CHATS=[-100])
        with patch("fosgen_pets.runtime.PostgresPetStore") as constructor, self.assertLogs("fosgen_pets.runtime", "WARNING") as logs:
            runtime = await start_pet_runtime(app, database_url="postgresql://private", environ={
                "FOSGEN_PETS_ENABLED": "1", "FOSGEN_PETS_CHAT_IDS": "-100", "FOSGEN_PETS_NEW_ASSET_VERSION": "3"})
        self.assertIsNone(runtime)
        constructor.assert_not_called()
        self.assertIn("reason=invalid_config", " ".join(logs.output))
        self.assertNotIn("private", " ".join(logs.output))
        self.assertEqual(app.ALLOWED_CHATS, [-100])

    async def test_only_approved_v3_passes_new_version_to_the_store(self):
        from fosgen_pets.runtime import start_pet_runtime
        for version, approved, expected_kwargs in ((None, None, {}), ("2", "0", {}), ("3", "1", {"asset_version": 3})):
            store = SimpleNamespace(initialize=AsyncMock(), close=AsyncMock())
            env = {"FOSGEN_PETS_ENABLED": "1", "FOSGEN_PETS_CHAT_IDS": "-100"}
            if version is not None:
                env["FOSGEN_PETS_NEW_ASSET_VERSION"] = version
            if approved is not None:
                env["FOSGEN_PETS_V3_ART_APPROVED"] = approved
            with self.subTest(version=version), \
                    patch("fosgen_pets.runtime.PostgresPetStore", return_value=store) as constructor, \
                    patch("fosgen_pets.runtime.register_pet_handlers", return_value="service"):
                runtime = await start_pet_runtime(SimpleNamespace(ALLOWED_CHATS=[-100]), database_url="postgresql://local", environ=env)
                self.assertIsNotNone(runtime)
                self.assertEqual(runtime.service, "service")
                constructor.assert_called_once_with("postgresql://local", **expected_kwargs)
                await runtime.stop()


class V3StoreConfigurationTests(unittest.TestCase):
    def test_store_rejects_invalid_and_legacy_new_issuance_versions(self):
        from fosgen_pets.postgres import PostgresPetStore
        for version in (True, False, 0, 1, 4, 3.0, "3", None):
            with self.subTest(version=version), self.assertRaises(ValueError):
                PostgresPetStore("postgresql://local", asset_version=version)


if __name__ == "__main__":
    unittest.main()

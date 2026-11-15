"""Real plush raster contracts; hashes alone do not establish art quality."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256
from io import BytesIO
import base64
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET

import numpy as np
from PIL import Image

from fosgen_pets.core import Appearance


def appearance(species="kitten", **fields):
    values = dict(species=species, palette="cream", pattern="solid", eyes="amber",
                  marking="none", marking_variant=0, asset_version=3, pattern_seed=123456)
    values.update(fields)
    return Appearance(**values)


class PlushRenderTests(unittest.TestCase):
    def test_actual_png_and_standalone_svg_are_the_same_portrait(self):
        from fosgen_pets.graphics_v3 import render_v3
        svg, png = render_v3(appearance())
        with Image.open(BytesIO(png)) as picture:
            self.assertEqual(picture.size, (512, 512))
            self.assertEqual(picture.mode, "RGB")
            self.assertGreater(np.asarray(picture).std(), 20)
        root = ET.fromstring(svg)
        images = list(root.iter("{http://www.w3.org/2000/svg}image"))
        self.assertEqual(len(images), 1)
        uri = images[0].attrib["href"]
        self.assertTrue(uri.startswith("data:image/png;base64,"))
        self.assertEqual(base64.b64decode(uri.split(",", 1)[1]), png)
        self.assertNotIn("http://", uri)

    def test_first_six_have_intact_silhouettes_and_masked_features(self):
        from fosgen_pets.graphics_v3 import render_layers
        palettes = {"kitten":"cream", "puppy":"golden", "fox":"red", "badger":"grey",
                    "capybara":"brown", "otter":"chocolate"}
        for species, palette in palettes.items():
            pattern = "mask" if species == "badger" else "solid"
            with self.subTest(species=species):
                layers = render_layers(appearance(species, palette=palette, pattern=pattern, marking="heart", marking_variant=12))
                silhouette = np.asarray(layers.silhouette)
                eyes = np.asarray(layers.eyes)
                markings = np.asarray(layers.markings)
                box = layers.silhouette.getbbox()
                self.assertTrue(all(12 <= value <= 500 for value in box), box)
                self.assertEqual(int(eyes[silhouette == 0].sum()), 0)
                self.assertEqual(int(markings[silhouette == 0].sum()), 0)
                self.assertGreater(int((eyes > 32).sum()), 50)
                self.assertGreater(int((markings > 32).sum()), 5)

    def test_local_render_is_byte_deterministic_and_thread_safe(self):
        from fosgen_pets.graphics_v3 import render_v3
        dna = appearance(pattern="tabby")
        expected = render_v3(dna)
        with ThreadPoolExecutor(max_workers=3) as workers:
            actual = list(workers.map(render_v3, [dna] * 6))
        self.assertTrue(all(value == expected for value in actual))
        self.assertNotEqual(sha256(expected[1]).digest(), sha256(render_v3(replace(dna, pattern_seed=123457))[1]).digest())

    def test_invalid_or_legacy_recipes_never_enter_plush_renderer(self):
        from fosgen_pets.graphics_v3 import render_v3
        for value in (replace(appearance(), asset_version=2), replace(appearance(), species="../../secret"),
                      replace(appearance(), pattern_seed=True), replace(appearance(), marking_variant=1)):
            with self.assertRaises(ValueError):
                render_v3(value)

    def test_seed_changes_iris_hue_without_tinting_pupils_or_catchlights(self):
        from fosgen_pets.graphics_v3 import _eye_tint
        pixels = np.full((16, 16, 3), (100, 65, 36), dtype=np.float32)
        pixels[0] = (5, 5, 5)
        pixels[1] = (250, 250, 250)
        mask = np.ones((16, 16), dtype=np.float32)
        first = _eye_tint(pixels, mask, appearance(pattern_seed=0))
        second = _eye_tint(pixels, mask, appearance(pattern_seed=999999))
        self.assertFalse(np.allclose(first[2:], second[2:], atol=.01))
        np.testing.assert_array_equal(first[:2], pixels[:2])

    def test_long_ears_do_not_place_rare_forehead_mark_in_empty_air(self):
        from fosgen_pets.graphics_v3 import render_layers
        for species, palette in (("bunny", "brown"), ("alpaca", "brown"), ("fawn", "tan")):
            with self.subTest(species=species):
                result = render_layers(appearance(species, palette=palette, marking="heart", marking_variant=12))
                self.assertGreater(int((np.asarray(result.markings) > 64).sum()), 25)
                if species == "bunny":
                    ys, _ = np.nonzero(np.asarray(result.markings) > 64)
                    self.assertGreater(float(ys.mean()), 170, "The forehead is below the ear roots")

    def test_freckles_remain_visible_on_native_white_muzzles(self):
        from fosgen_pets.graphics_v3 import render_layers
        for species, palette in (("fox", "red"), ("bunny", "white"), ("alpaca", "white")):
            base = appearance(species, palette=palette, pattern_seed=314159)
            with self.subTest(species=species):
                plain = np.asarray(render_layers(base).image).astype(np.int16)
                marked = np.asarray(render_layers(replace(base, marking="freckle", marking_variant=2)).image).astype(np.int16)
                self.assertGreater(int((np.max(np.abs(plain - marked), axis=2) > 8).sum()), 5)

    def test_nostril_protection_does_not_restore_ivory_fur_on_dark_foal(self):
        from fosgen_pets.graphics_v3 import render_layers, _soft_ellipses
        from fosgen_pets.plush_assets import load_master
        master, record = load_master("foal")
        original = np.asarray(master)[..., :3]
        # Disable only anatomical warping to inspect the exact source material.
        with patch("fosgen_pets.graphics_v3._warp", side_effect=lambda image, mesh: image):
            result = render_layers(appearance("foal", palette="black"))
        nose = _soft_ellipses(record["nose_boxes"])
        bright_fur = (nose > .5) & (original.min(axis=2) > 190)
        self.assertGreater(int(bright_fur.sum()), 15)
        self.assertLess(float(np.asarray(result.subject)[..., :3][bright_fur].mean()), 100)

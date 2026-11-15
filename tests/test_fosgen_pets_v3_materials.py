"""Raster material contracts, independent of the authored plush masters."""
from concurrent.futures import ThreadPoolExecutor
from collections import deque
from importlib import import_module, util
import tracemalloc
import unittest

import numpy as np

from fosgen_pets.core import Appearance, MARKING_VARIANT_COUNTS, PALETTES, PATTERNS


def appearance(species="kitten", palette="ginger", pattern="tabby", seed=17,
               marking="none", variant=0):
    return Appearance(species, palette, pattern, "amber", marking, variant,
                      asset_version=3, pattern_seed=seed)


class PlushMaterialTests(unittest.TestCase):
    def materials(self):
        self.assertIsNotNone(util.find_spec("fosgen_pets.plush_materials"),
                             "v3 needs deterministic raster coat fields")
        return import_module("fosgen_pets.plush_materials")

    def test_all_species_palette_patterns_are_finite_bounded_rgb(self):
        materials = self.materials()
        for species, palettes in PALETTES.items():
            for palette in palettes:
                for pattern in PATTERNS[species]:
                    with self.subTest(species=species, palette=palette, pattern=pattern):
                        field = materials.color_field(
                            appearance(species, palette, pattern), (80, 96))
                        self.assertEqual(field.shape, (96, 80, 3))
                        self.assertEqual(field.dtype, np.float32)
                        self.assertTrue(np.isfinite(field).all())
                        self.assertGreaterEqual(float(field.min()), 0)
                        self.assertLessEqual(float(field.max()), 255)
                        self.assertLessEqual(field.nbytes, 96 * 80 * 3 * 4)

    def test_calls_are_deterministic_and_do_not_share_random_state(self):
        materials = self.materials()
        dna = appearance()
        first = materials.color_field(dna, (128, 128))
        with ThreadPoolExecutor(max_workers=4) as pool:
            concurrent = list(pool.map(lambda _: materials.color_field(dna, (128, 128)), range(8)))
        for result in concurrent:
            np.testing.assert_array_equal(first, result)
        # Eye and marking choices must not move a coat's pattern.
        unrelated = Appearance("kitten", "ginger", "tabby", "blue", "heart", 31,
                               asset_version=3, pattern_seed=17)
        np.testing.assert_array_equal(first, materials.color_field(unrelated, (128, 128)))

    def test_default_surface_peak_memory_is_bounded(self):
        materials = self.materials()
        tracemalloc.start()
        try:
            field = materials.color_field(appearance())
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertEqual(field.nbytes, 512 * 512 * 3 * 4)
        self.assertLess(peak, 64 * 1024 * 1024)

    def test_seed_changes_pattern_layout_substantially(self):
        materials = self.materials()
        cases = (("kitten", "ginger", "tabby", 3.0),
                 ("badger", "grey", "mask", 1.5),
                 ("foal", "grey", "dapple", 0.3),
                 ("puppy", "cream", "spotted", 3.0))
        for species, palette, pattern, threshold in cases:
            first = materials.color_field(appearance(species, palette, pattern, 2), (192, 192))
            second = materials.color_field(appearance(species, palette, pattern, 83921), (192, 192))
            with self.subTest(species=species):
                self.assertGreater(float(np.abs(first - second).mean()), threshold)

    def test_uniform_species_do_not_gain_large_patches(self):
        materials = self.materials()
        for species, palette in (("capybara", "brown"), ("otter", "brown")):
            field = materials.color_field(appearance(species, palette, "solid"), (160, 160))
            luminance = field.mean(axis=2)
            self.assertLess(float(luminance.max() - luminance.min()), 20.0)
            self.assertLess(float(luminance.std()), 4.5)

    def test_dapples_are_filled_soft_light_fur_not_dark_rings(self):
        materials = self.materials()
        solid = materials.color_field(appearance("foal", "grey", "solid"), (256, 256))
        dapple = materials.color_field(appearance("foal", "grey", "dapple"), (256, 256))
        delta = (dapple - solid).mean(axis=2)
        self.assertGreater(float(delta.max()), 7)
        self.assertGreaterEqual(float(delta.min()), -0.001)
        bright = delta > 5
        self.assertGreater(float(bright.mean()), 0.005)
        self.assertLess(float(bright.mean()), 0.20)
        # Every scanline's isolated bright interval has a filled centre.
        y, x = np.unravel_index(int(delta.argmax()), delta.shape)
        self.assertGreater(float(delta[y, x]), float(delta[y, max(x - 15, 0)]))
        # Flood-fill the dark outside. Large enclosed dark centres would mean
        # annular dapples; a few single-pixel holes from touching hairs are fine.
        outside = np.zeros_like(bright)
        queue = deque([(0, 0)])
        outside[0, 0] = True
        while queue:
            row, column = queue.popleft()
            for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
                yy, xx = row + dy, column + dx
                if (0 <= yy < bright.shape[0] and 0 <= xx < bright.shape[1]
                        and not bright[yy, xx] and not outside[yy, xx]):
                    outside[yy, xx] = True
                    queue.append((yy, xx))
        self.assertLess(int((~bright & ~outside).sum()), 20)

    def test_badger_blaze_and_raccoon_band_follow_eye_anatomy(self):
        materials = self.materials()
        anchors = {"head": (.22, .14, .78, .62),
                   "eyes": ((.38, .37), (.62, .37)), "nose": (.50, .52)}
        badger = materials.color_field(appearance("badger", "grey", "mask"),
                                       (200, 200), anchors=anchors)
        raccoon = materials.color_field(appearance("raccoon", "grey", "mask"),
                                        (200, 200), anchors=anchors)
        for x in (76, 123):
            self.assertLess(float(badger[74, x].mean()), 100)
            self.assertLess(float(raccoon[74, x].mean()), 100)
        self.assertGreater(float(badger[74, 100].mean()), 205)
        self.assertLess(float(raccoon[74, 100].mean()), 100)

    def test_arctic_fox_and_pale_patchwork_keep_natural_coat_contrast(self):
        materials = self.materials()
        arctic = materials.color_field(appearance("fox", "arctic", "solid"), (128, 128))
        self.assertGreater(float(arctic.min()), 220)
        for species, palette, pattern in (("bunny", "white", "broken"),
                                          ("alpaca", "white", "piebald"),
                                          ("calf", "black", "spotted")):
            field = materials.color_field(appearance(species, palette, pattern), (128, 128))
            self.assertGreater(float(field.mean(axis=2).max() - field.mean(axis=2).min()), 35)

    def test_all_canonical_marking_variants_are_finite_and_distinct(self):
        materials = self.materials()
        for marking, count in MARKING_VARIANT_COUNTS.items():
            masks = []
            for variant in range(count):
                mask = materials.marking_mask(
                    appearance(marking=marking, variant=variant), (192, 192))
                self.assertEqual(mask.shape, (192, 192))
                self.assertEqual(mask.dtype, np.float32)
                self.assertTrue(np.isfinite(mask).all())
                self.assertGreaterEqual(float(mask.min()), 0)
                self.assertLessEqual(float(mask.max()), 1)
                if marking == "none":
                    self.assertEqual(float(mask.max()), 0)
                else:
                    self.assertGreater(float(mask.max()), .6)
                    self.assertLess(float(mask.mean()), .035)
                masks.append(mask.tobytes())
            self.assertEqual(len(set(masks)), count)

    def test_markings_follow_forehead_and_foot_anchors(self):
        materials = self.materials()
        anchors = {"head": (.2, .1, .8, .6),
                   "eyes": ((.37, .35), (.63, .35)),
                   "nose": (.50, .46), "feet": ((.3, .85), (.7, .85))}
        forehead = materials.marking_mask(appearance(marking="star", variant=17),
                                          (200, 200), anchors=anchors)
        sock = materials.marking_mask(appearance(marking="sock_left", variant=4),
                                      (200, 200), anchors=anchors)
        fy, fx = np.unravel_index(int(forehead.argmax()), forehead.shape)
        sy, sx = np.unravel_index(int(sock.argmax()), sock.shape)
        self.assertTrue(.40 < fx / 199 < .60 and .12 < fy / 199 < .32)
        self.assertTrue(.22 < sx / 199 < .38 and .73 < sy / 199 < .91)

    def test_invalid_or_oversized_sizes_are_rejected(self):
        materials = self.materials()
        for size in ((0, 512), (512, -1), (4096, 4096), (16.5, 16), (True, 64)):
            with self.assertRaises((TypeError, ValueError)):
                materials.color_field(appearance(), size)


if __name__ == "__main__":
    unittest.main()

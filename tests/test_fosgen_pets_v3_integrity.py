"""Actual v3 assets, anatomical invariants and visual seed variation."""
from collections import deque
from dataclasses import replace
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from fosgen_pets.core import Appearance, V3_SPECIES, V3_PALETTES, V3_PATTERNS
from fosgen_pets.graphics_v3 import render_layers
from fosgen_pets.portrait import render_portrait
from fosgen_pets import plush_assets


def component_fraction(mask):
    """Largest connected mass after reducing away individual fur fibres."""
    pixels = np.asarray(mask.resize((96, 96), Image.Resampling.BOX)) > 96
    remaining = set(map(tuple, np.argwhere(pixels)))
    total, largest = len(remaining), 0
    while remaining:
        queue = deque([remaining.pop()])
        count = 0
        while queue:
            y, x = queue.popleft()
            count += 1
            for point in ((y-1, x), (y+1, x), (y, x-1), (y, x+1)):
                if point in remaining:
                    remaining.remove(point)
                    queue.append(point)
        largest = max(largest, count)
    return largest / max(total, 1)


class PlushIntegrityTests(unittest.TestCase):
    def test_all_species_patterns_keep_features_on_one_uncropped_subject(self):
        records = plush_assets.catalog()
        for species in V3_SPECIES:
            for index, pattern in enumerate(V3_PATTERNS[species]):
                seed = (0, 999999)[index % 2]
                dna = Appearance(species, V3_PALETTES[species][0], pattern,
                                 "blue", "star", 34, 3, seed)
                with self.subTest(species=species, pattern=pattern, seed=seed):
                    layers = render_layers(dna)
                    alpha = np.asarray(layers.silhouette)
                    box = layers.silhouette.getbbox()
                    self.assertTrue(8 <= box[0] < box[2] <= 504, box)
                    self.assertTrue(8 <= box[1] < box[3] <= 504, box)
                    self.assertGreater(component_fraction(layers.silhouette), .985)
                    for mask in (layers.fur, layers.eyes, layers.markings):
                        self.assertFalse(np.asarray(mask)[alpha == 0].any())
                    self.assertGreater(int((np.asarray(layers.markings) > 64).sum()), 10)
                    # Both eyes follow the same mesh as the fur and remain near
                    # their species' authored guides, including side-facing eyes.
                    for left, top, right, bottom in records[species]["eye_boxes"]:
                        eye = np.asarray(layers.eyes)[max(0, top-5):bottom+5, max(0, left-5):right+5]
                        self.assertGreater(int((eye > 64).sum()), 15)

    def test_released_v3_png_pixels_match_frozen_goldens_for_all_species(self):
        fixtures = json.loads(Path(__file__).with_name("fixtures").joinpath("fosgen_pets_v3.json").read_text())
        self.assertEqual({item["dna"]["species"] for item in fixtures}, set(V3_SPECIES))
        for fixture in fixtures:
            dna = Appearance(**fixture["dna"])
            with self.subTest(species=dna.species):
                portrait = render_portrait(dna)
                self.assertEqual(dna.fingerprint(), fixture["dna_sha256"])
                self.assertEqual(portrait.pixel_hash, fixture["rgba_sha256"])
                self.assertEqual(sha256(portrait.png).hexdigest(), fixture["png_sha256"])
                self.assertEqual(portrait.renderer_version, "fosgen-plush-v3")

    def test_patterned_seed_pairs_change_visible_structure_not_only_hashes(self):
        from fosgen_pets.visual_metrics import compare_images
        cases = (("kitten", "ginger", "tabby"), ("kitten", "black", "tortoiseshell"),
                 ("puppy", "cream", "spotted"), ("badger", "grey", "mask"),
                 ("foal", "grey", "dapple"))
        for species, palette, pattern in cases:
            dna = Appearance(species, palette, pattern, "amber", "none", 0, 3, 0)
            with self.subTest(species=species, pattern=pattern):
                first = render_portrait(dna).png
                second = render_portrait(replace(dna, pattern_seed=999999)).png
                metrics = compare_images(first, second, species=species, pattern=pattern)
                self.assertFalse(metrics["identical_rgb"])
                self.assertGreater(metrics["changed_pixel_ratio"], .004)
                self.assertLess(metrics["foreground_ssim"], .995)
        # Arctic solids retain natural white fur; smaller differences are valid.
        dna = Appearance("fox", "arctic", "solid", "amber", "none", 0, 3, 0)
        first = render_portrait(dna).png
        second = render_portrait(replace(dna, pattern_seed=999999)).png
        metrics = compare_images(first, second, species="fox", pattern="solid")
        self.assertFalse(metrics["identical_rgb"])
        self.assertEqual(metrics["diagnostic"]["review_level"], "info")

    def test_checksum_guards_reject_catalog_or_master_replacements(self):
        root = plush_assets.ASSET_ROOT
        with tempfile.TemporaryDirectory() as folder:
            temporary = Path(folder)
            (temporary / "anchor-data.json").write_bytes((root / "anchor-data.json").read_bytes() + b" ")
            with patch.object(plush_assets, "ASSET_ROOT", temporary), self.assertRaises(ValueError):
                plush_assets.catalog()
            (temporary / "anchor-data.json").write_bytes((root / "anchor-data.json").read_bytes())
            (temporary / "kitten-master.webp").write_bytes(b"replaced")
            with patch.object(plush_assets, "ASSET_ROOT", temporary), self.assertRaises(ValueError):
                plush_assets.load_master("kitten")

    def test_decoded_masters_are_not_shared_mutable_images(self):
        first, _ = plush_assets.load_master("kitten")
        original = first.getpixel((256, 256))
        first.putpixel((256, 256), (255, 0, 255, 255))
        second, _ = plush_assets.load_master("kitten")
        self.assertEqual(second.getpixel((256, 256)), original)
        self.assertIsNot(first, second)
        for name in ("../kitten", "file:///tmp/pet", "https://example.com/pet", "", None):
            with self.subTest(name=name), self.assertRaises(ValueError):
                plush_assets.load_master(name)

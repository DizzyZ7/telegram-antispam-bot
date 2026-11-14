"""Renderer inputs are finite recipes, never executable SVG or file paths."""
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from io import BytesIO
import unittest
from xml.etree import ElementTree as ET

from PIL import Image

from fosgen_pets.core import Appearance, build_appearance
from fosgen_pets.portrait import render_portrait
from fosgen_pets.render_svg import render_svg


class PortraitSecurityTests(unittest.TestCase):
    def test_untrusted_recipe_cannot_inject_svg_or_external_resources(self):
        a = Appearance("kitten", "cream", "solid", "brown", "none", 0)
        for field in ("species", "palette", "pattern", "eyes", "marking"):
            for value in ('"><script>alert(1)</script>', "file:///etc/passwd", "https://evil.invalid/image.svg", []):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    render_svg(replace(a, **{field: value}))

    def test_numeric_coercion_and_unknown_versions_fail_closed(self):
        a = Appearance("kitten", "cream", "solid", "brown", "none", 0)
        for field, value in (("asset_version", True), ("asset_version", 99), ("marking_variant", -1),
                             ("marking_variant", True), ("marking_variant", 4096), ("marking_variant", "1")):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                render_svg(replace(a, **{field: value}))

    def test_portrait_hash_identifies_actual_pixels_and_fixed_dimensions(self):
        # Historical art works before v2, too; hash is not DNA or encoded-file hash.
        p = render_portrait(build_appearance("foal", "security-fixture", asset_version=1))
        with Image.open(BytesIO(p.png)) as image:
            self.assertEqual(image.size, (512, 512))
            self.assertEqual(p.pixel_hash, sha256(image.convert("RGBA").tobytes()).hexdigest())
        root = ET.fromstring(p.svg)
        self.assertFalse(any(node.tag.endswith(("image", "script", "foreignObject")) for node in root.iter()))
        self.assertEqual(p.renderer_version, "fosgen-svg-v1")

    def test_parallel_rasterization_keeps_exact_bytes(self):
        a = build_appearance("calf", "parallel-fixture", asset_version=1)
        expected = render_portrait(a)
        with ThreadPoolExecutor(max_workers=6) as workers:
            results = list(workers.map(render_portrait, [a] * 10))
        self.assertTrue(all(p == expected for p in results))

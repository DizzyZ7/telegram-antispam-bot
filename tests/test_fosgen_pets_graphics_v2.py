"""Raster contracts for the authored v2 pet portraits.

The comparisons intentionally inspect rendered pixels: a changed seed must
change visible coat art, and coats/markings must obey anatomical masks.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from importlib import import_module, util
from io import BytesIO
import itertools
import random
import unittest
from xml.etree import ElementTree as ET

import cairosvg
from PIL import Image, ImageChops, ImageFilter

from fosgen_pets.core import Appearance, MARKINGS, PALETTES, PATTERNS, SPECIES


NS = "{http://www.w3.org/2000/svg}"
# Art direction fixtures, independent of the implementation's anchor table.
EYE_CENTERS = {
    "kitten": ((213, 238), (297, 238)),
    "puppy": ((214, 243), (298, 243)),
    "foal": ((216, 226), (292, 226)),
    "calf": ((207, 236), (303, 236)),
    "bunny": ((210, 246), (300, 246)),
    "fox": ((213, 234), (297, 234)),
    "badger": ((211, 244), (301, 244)),
    "fawn": ((215, 239), (295, 239)),
    "raccoon": ((211, 244), (301, 244)),
    "capybara": ((205, 232), (305, 232)),
    "alpaca": ((218, 211), (294, 211)),
    "otter": ((213, 246), (297, 246)),
}


def png(svg: str, size: int = 256) -> Image.Image:
    return Image.open(BytesIO(cairosvg.svg2png(
        bytestring=svg.encode("utf-8"), output_width=size, output_height=size,
    ))).convert("RGBA")


def selected_svg(root: ET.Element, elements: list[ET.Element]) -> str:
    isolated = ET.Element(NS + "svg", {
        "viewBox": "0 0 512 512", "width": "512", "height": "512",
    })
    defs = root.find(NS + "defs")
    if defs is not None:
        isolated.append(deepcopy(defs))
    for element in elements:
        isolated.append(deepcopy(element))
    return ET.tostring(isolated, encoding="unicode")


def changed_pixels(first: Image.Image, second: Image.Image, threshold: int = 8) -> int:
    difference = ImageChops.difference(first.convert("RGB"), second.convert("RGB"))
    red, green, blue = difference.split()
    maximum = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    return sum(maximum.histogram()[threshold + 1:])


class GraphicsV2Tests(unittest.TestCase):
    def renderer(self):
        self.assertIsNotNone(
            util.find_spec("fosgen_pets.graphics_v2"),
            "v2 needs a real renderer for all twelve species",
        )
        return import_module("fosgen_pets.graphics_v2").render_v2

    def appearance(self, species="kitten", *, seed=123456, palette=None,
                   pattern=None, marking="none", variant=0, eyes="amber"):
        return Appearance(
            species, palette or PALETTES[species][0], pattern or PATTERNS[species][0],
            eyes, marking, variant, asset_version=2, pattern_seed=seed,
        )

    def test_v2_renderer_exists(self):
        self.assertTrue(callable(self.renderer()))

    def test_every_catalog_combination_is_a_self_contained_svg(self):
        render = self.renderer()
        for species in SPECIES:
            for palette, pattern in itertools.product(PALETTES[species], PATTERNS[species]):
                with self.subTest(species=species, palette=palette, pattern=pattern):
                    svg = render(self.appearance(species, palette=palette, pattern=pattern))
                    root = ET.fromstring(svg)
                    self.assertEqual(root.attrib["viewBox"], "0 0 512 512")
                    self.assertEqual(root.attrib["data-asset-version"], "2")
                    ids = {node.attrib["id"] for node in root.iter() if "id" in node.attrib}
                    for node in root.iter():
                        self.assertNotIn(node.tag.rsplit("}", 1)[-1], {"script", "image", "foreignObject", "use"})
                        for key, value in node.attrib.items():
                            self.assertFalse(key.lower().startswith("on"))
                            self.assertNotIn("href", key)
                            if "url(" in value:
                                self.assertTrue(value.startswith("url(#") and value.endswith(")"))
                                self.assertIn(value[5:-1], ids)

    def test_solid_none_seed_changes_visible_coat_for_every_species(self):
        render = self.renderer()
        for species in SPECIES:
            pattern = "solid" if "solid" in PATTERNS[species] else PATTERNS[species][0]
            images = [png(render(self.appearance(species, seed=seed, pattern=pattern)))
                      for seed in (0, 1, 19, 50201, 999999)]
            for first, second in itertools.combinations(images, 2):
                with self.subTest(species=species):
                    self.assertGreater(changed_pixels(first, second), 200)

    def test_every_pattern_rasterizes_and_changes_visible_pixels(self):
        render = self.renderer()
        for species in SPECIES:
            portraits = [png(render(self.appearance(species, pattern=pattern)))
                         for pattern in PATTERNS[species]]
            for first, second in itertools.combinations(portraits, 2):
                with self.subTest(species=species):
                    self.assertGreater(changed_pixels(first, second), 80)

    def test_fawn_spots_are_small_pale_flank_spots(self):
        render = self.renderer()
        root = ET.fromstring(render(self.appearance("fawn", pattern="spotted")))
        group = next(node for node in root.iter(NS + "g")
                     if node.get("data-layer") == "pattern" and node.get("data-part") == "body")
        image = png(selected_svg(root, [group]), 512)
        remaining = {index for index, value in enumerate(image.getchannel("A").tobytes()) if value > 128}
        self.assertGreater(len(remaining), 400)
        components = []
        while remaining:
            start = remaining.pop()
            pending = [start]
            component = [start]
            while pending:
                index = pending.pop()
                for neighbor in (index - 1, index + 1, index - 512, index + 512):
                    if neighbor in remaining:
                        remaining.remove(neighbor)
                        pending.append(neighbor)
                        component.append(neighbor)
            components.append(component)
        self.assertGreaterEqual(len(components), 10)
        self.assertLess(max(map(len, components)), 180)
        for component in components:
            center = component[len(component) // 2]
            self.assertGreater(min(image.getpixel((center % 512, center // 512))[:3]), 210)

    def test_noncanonical_v2_marking_variants_are_rejected(self):
        render = self.renderer()
        for marking, limit in (("none", 1), ("star", 35), ("heart", 35),
                               ("blaze", 35), ("freckle", 4),
                               ("sock_left", 9), ("sock_right", 9)):
            with self.subTest(marking=marking), self.assertRaises(ValueError):
                render(self.appearance(marking=marking, variant=limit))

    def test_every_supported_marking_variant_changes_actual_pixels(self):
        render = self.renderer()
        for marking, count in (("star", 35), ("heart", 35), ("blaze", 35),
                               ("freckle", 4), ("sock_left", 9), ("sock_right", 9)):
            with self.subTest(marking=marking):
                images = {png(render(self.appearance(marking=marking, variant=variant)), 512).tobytes()
                          for variant in range(count)}
                self.assertEqual(len(images), count)

    def test_pattern_and_marking_pixels_stay_in_their_anatomical_masks(self):
        render = self.renderer()
        for species in SPECIES:
            for pattern in PATTERNS[species]:
                root = ET.fromstring(render(self.appearance(
                    species, pattern=pattern, marking="blaze", variant=21,
                )))
                for group in root.iter(NS + "g"):
                    if group.get("data-layer") not in {"pattern", "coat-light", "marking"}:
                        continue
                    part = group.get("data-part")
                    base = next(node for node in root.iter(NS + "path")
                                if node.get("data-layer") == "base" and node.get("data-part") == part)
                    mask = deepcopy(base)
                    mask.attrib.clear()
                    mask.set("d", base.attrib["d"])
                    mask.set("fill", "#ffffff")
                    mask_alpha = png(selected_svg(root, [mask]), 512).getchannel("A")
                    drawn_alpha = png(selected_svg(root, [group]), 512).getchannel("A")
                    # Cairo's clipping and fill antialiasing differ at fractional
                    # edge pixels. Allow only that one-pixel raster fringe;
                    # visible paint farther outside the silhouette is a leak.
                    covered = mask_alpha.point(lambda value: 255 if value else 0).filter(ImageFilter.MaxFilter(3))
                    outside = ImageChops.multiply(drawn_alpha, ImageChops.invert(covered))
                    self.assertEqual(outside.getextrema()[1], 0, (species, pattern, part))

    def test_eyes_remain_inside_heads_and_visible_above_muzzles(self):
        render = self.renderer()
        for species, centers in EYE_CENTERS.items():
            root = ET.fromstring(render(self.appearance(species)))
            eye_group = next(node for node in root.iter(NS + "g") if node.get("data-layer") == "eyes")
            isolated = png(selected_svg(root, [eye_group]), 512)
            full = png(ET.tostring(root, encoding="unicode"), 512)
            head = next(node for node in root.iter(NS + "path")
                        if node.get("data-layer") == "base" and node.get("data-part") == "head")
            head_shape = deepcopy(head)
            head_shape.set("stroke", "none")
            head_shape.set("fill", "#fff")
            head_alpha = png(selected_svg(root, [head_shape]), 512).getchannel("A")
            outside = ImageChops.multiply(isolated.getchannel("A"), ImageChops.invert(head_alpha))
            self.assertEqual(outside.getextrema()[1], 0, species)
            for x, y in centers:
                with self.subTest(species=species, eye=(x, y)):
                    self.assertGreater(isolated.getpixel((x, y))[3], 250)
                    self.assertLess(max(full.getpixel((x, y))[:3]), 100)
                    self.assertGreater(min(full.getpixel((x - 3, y - 6))[:3]), 220)

    def test_socks_change_only_the_named_front_leg(self):
        render = self.renderer()
        for species in SPECIES:
            neutral = png(render(self.appearance(species)), 512)
            for marking, side in (("sock_left", "left"), ("sock_right", "right")):
                svg = render(self.appearance(species, marking=marking, variant=4))
                image = png(svg, 512)
                difference = ImageChops.difference(neutral.convert("RGB"), image.convert("RGB"))
                box = difference.getbbox()
                self.assertIsNotNone(box, (species, marking))
                self.assertGreater(box[1], 340, (species, marking, box))
                self.assertLess(box[2], 257) if side == "left" else self.assertGreater(box[0], 255)

    def test_forehead_and_cheek_markings_are_visible_and_bounded(self):
        render = self.renderer()
        for species in SPECIES:
            neutral = png(render(self.appearance(species)), 512)
            for marking in ("star", "heart", "blaze", "freckle"):
                with self.subTest(species=species, marking=marking):
                    variant = 2 if marking == "freckle" else 17
                    image = png(render(self.appearance(species, marking=marking, variant=variant)), 512)
                    difference = ImageChops.difference(neutral.convert("RGB"), image.convert("RGB"))
                    box = difference.getbbox()
                    self.assertIsNotNone(box)
                    self.assertLess(box[3], 320)
                    self.assertGreater(changed_pixels(neutral, image), 20)

    def test_local_streams_ignore_other_features_and_global_random_state(self):
        render = self.renderer()
        appearance = self.appearance("calf", pattern="piebald")
        expected = render(appearance)
        state = random.getstate()
        random.seed(98129)
        try:
            self.assertEqual(render(appearance), expected)
        finally:
            random.setstate(state)
        def pattern_geometry(svg):
            root = ET.fromstring(svg)
            return [ET.tostring(group, encoding="unicode") for group in root.iter(NS + "g")
                    if group.get("data-layer") == "pattern"]
        self.assertEqual(pattern_geometry(expected), pattern_geometry(render(
            self.appearance("calf", pattern="piebald", eyes="blue", marking="star", variant=14),
        )))

    def test_concurrent_renders_are_byte_identical(self):
        render = self.renderer()
        inputs = [self.appearance(species, seed=seed, marking="heart", variant=13)
                  for species in SPECIES for seed in (17, 922)]
        expected = [render(value) for value in inputs]
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(list(pool.map(render, inputs * 3)), expected * 3)


if __name__ == "__main__":
    unittest.main()

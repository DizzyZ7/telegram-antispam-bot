"""Portable gallery files and honest version/seed comparison coverage."""
from collections import defaultdict
from contextlib import ExitStack, contextmanager
from hashlib import sha256
from html.parser import HTMLParser
from importlib import import_module, util
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from fosgen_pets.core import PALETTES, PATTERNS
from fosgen_pets.portrait import Portrait


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        self.links.extend(value for key, value in attrs if key in {"src", "href"})


def small_portrait(appearance):
    """Replace only expensive artwork, preserving its complete PNG contract."""
    digest = sha256(appearance.canonical_dna().encode()).digest()
    image = Image.new("RGB", (512, 512), "#fff7ed")
    drawing = ImageDraw.Draw(image)
    drawing.ellipse((70, 70, 440, 470), fill=tuple(digest[:3]))
    drawing.rectangle((180, 220, 210, 245), fill=tuple(digest[3:6]))
    output = BytesIO()
    image.save(output, format="PNG")
    return Portrait("<svg xmlns='http://www.w3.org/2000/svg'/>", output.getvalue(),
                    sha256(image.convert("RGBA").tobytes()).hexdigest(),
                    f"fixture-v{appearance.asset_version}")


class GalleryV3Tests(unittest.TestCase):
    def gallery(self):
        self.assertIsNotNone(util.find_spec("fosgen_pets.gallery_v3"),
                             "v3 needs a portable gallery of rendered PNGs")
        return import_module("fosgen_pets.gallery_v3")

    @contextmanager
    def catalog(self, gallery):
        # Two real species keep generation cheap while testing actual file and
        # metric behavior. Full twelve-species rendering belongs to CLI review.
        patches = (
            patch.object(gallery, "SPECIES", ("kitten", "fox")),
            patch.object(gallery, "PALETTES", {key: PALETTES[key][:3]
                                             for key in ("kitten", "fox")}),
            patch.object(gallery, "PATTERNS", {"kitten": ("tabby", "tortoiseshell"),
                                              "fox": ("solid", "mask")}),
            patch.object(gallery, "render_portrait", side_effect=small_portrait),
        )
        with ExitStack() as stack:
            for replacement in patches:
                stack.enter_context(replacement)
            yield

    def test_quick_gallery_contains_real_pngs_and_same_gene_version_pairs(self):
        gallery = self.gallery()
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "gallery"
            with self.catalog(gallery):
                manifest = gallery.build_gallery(output, quick=True)
            self.assertEqual(manifest, json.loads((output / "metrics.json").read_text()))
            records = {item["id"]: item for item in manifest["images"]}
            self.assertEqual({item["appearance"]["species"] for item in records.values()},
                             {"kitten", "fox"})
            version_pairs = [pair for pair in manifest["comparisons"] if pair["kind"] == "v2-v3"]
            self.assertEqual(len(version_pairs), 2)
            for pair in version_pairs:
                first, second = records[pair["first"]], records[pair["second"]]
                genes2, genes3 = first["appearance"].copy(), second["appearance"].copy()
                self.assertEqual(genes2.pop("asset_version"), 2)
                self.assertEqual(genes3.pop("asset_version"), 3)
                self.assertEqual(genes2, genes3)
                self.assertIn("ssim", pair["metrics"])
                for record in (first, second):
                    with Image.open(output / record["png"]) as image:
                        self.assertEqual(image.size, (512, 512))
                        self.assertEqual(record["metrics"]["rgb_sha256"],
                                         sha256(image.convert("RGB").tobytes()).hexdigest())
            self.assertFalse(manifest["coverage"]["complete"])
            parser = Links()
            parser.feed((output / "index.html").read_text())
            for link in parser.links:
                if not link.startswith("#"):
                    self.assertFalse(":" in link, link)
                    self.assertTrue((output / link).is_file(), link)
            for key in ("hero", "comparison"):
                with Image.open(output / manifest["contact_sheets"][key]) as sheet:
                    self.assertGreater(sheet.width, 512)

    def test_full_gallery_covers_palettes_seed_series_and_all_rare_markings(self):
        gallery = self.gallery()
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            with self.catalog(gallery):
                manifest = gallery.build_gallery(output)
            appearances = [item["appearance"] for item in manifest["images"]
                           if item["appearance"]["asset_version"] == 3]
            palettes = defaultdict(set)
            for appearance in appearances:
                palettes[appearance["species"]].add(appearance["palette"])
            self.assertGreaterEqual(len(palettes["kitten"]), 3)
            self.assertGreaterEqual(len(palettes["fox"]), 3)
            for marking in ("star", "heart", "blaze", "freckle", "sock_left", "sock_right"):
                self.assertTrue(any(item["marking"] == marking for item in appearances), marking)
            seed_pairs = [pair for pair in manifest["comparisons"] if pair["kind"] == "seed"]
            self.assertTrue(seed_pairs)
            seeds = {item["pattern_seed"] for item in appearances
                     if item["species"] == "kitten" and item["pattern"] == "tortoiseshell"}
            self.assertGreaterEqual(len(seeds), 6)
            self.assertTrue(manifest["coverage"]["complete"])
            self.assertTrue((output / manifest["contact_sheets"]["seeds"]).is_file())
            self.assertTrue((output / manifest["contact_sheets"]["rare"]).is_file())
            for pair in seed_pairs:
                self.assertIn("foreground_ssim", pair["metrics"])
                self.assertIn("diagnostic", pair["metrics"])


if __name__ == "__main__":
    unittest.main()

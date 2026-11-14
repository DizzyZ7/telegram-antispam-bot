"""The offline QA gallery must support the same v2 catalog as /pettest."""
from pathlib import Path
import tempfile
import unittest
from xml.etree import ElementTree

from fosgen_pets.core import PATTERNS, SPECIES
from fosgen_pets.gallery import build_gallery


class GalleryTests(unittest.TestCase):
    def test_all_species_and_seed_comparisons_render_without_database(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            count = build_gallery(output)
            self.assertEqual(count, 102)
            self.assertEqual(len(list(output.glob("*.svg"))), 102)
            html = (output / "index.html").read_text()
            for species in SPECIES:
                for pattern in PATTERNS[species]:
                    for sample in range(3):
                        path = output / f"{species}_{pattern}_{sample}.svg"
                        root = ElementTree.fromstring(path.read_text())
                        self.assertEqual(root.attrib["data-asset-version"], "2")
            self.assertIn("999999", html)
            self.assertIn("Pattern Seed", html)

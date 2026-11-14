"""Contract tests for species-aware markings and SVG safety."""
import unittest
from xml.etree import ElementTree

from fosgen_pets.core import Appearance, PALETTES, PATTERNS
from fosgen_pets.render_svg import render_svg


class PatternTests(unittest.TestCase):
    def test_all_supported_pattern_combinations_parse(self):
        for species in ("kitten", "foal", "calf"):
            for palette in PALETTES[species]:
                for pattern in PATTERNS[species]:
                    dna = Appearance(species, palette, pattern, "amber", "star", 123)
                    svg = render_svg(dna)
                    root = ElementTree.fromstring(svg)
                    self.assertTrue(root.tag.endswith("svg"))
                    self.assertIn('clip-path="url(#fur)"', svg)

    def test_patterns_are_visually_different_svg(self):
        for species in ("kitten", "foal", "calf"):
            rendered = [
                render_svg(Appearance(species, PALETTES[species][0], pattern, "amber", "star", 123))
                for pattern in PATTERNS[species]
            ]
            self.assertEqual(len(rendered), len(set(rendered)))

    def test_marking_variants_affect_svg_when_marking_visible(self):
        first = Appearance("kitten", "cream", "solid", "brown", "heart", 1)
        second = Appearance("kitten", "cream", "solid", "brown", "heart", 2)
        self.assertNotEqual(render_svg(first), render_svg(second))

    def test_invalid_pattern_rejected(self):
        with self.assertRaises(ValueError):
            render_svg(Appearance("calf", "brown", "tabby", "brown", "star", 1))


if __name__ == "__main__":
    unittest.main()

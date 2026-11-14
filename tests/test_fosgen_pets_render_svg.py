"""Render contract: stable, masked, validated SVG prototypes."""
import unittest
from xml.etree import ElementTree

from fosgen_pets.core import build_appearance
from fosgen_pets.render_svg import render_svg


class PetSvgTests(unittest.TestCase):
    def test_three_species_have_parseable_svg(self):
        for species in ("kitten", "foal", "calf"):
            svg = render_svg(build_appearance(species, "owner:42", asset_version=1))
            root = ElementTree.fromstring(svg)
            self.assertTrue(root.tag.endswith("svg"))
            self.assertIn('clip-path="url(#fur)"', svg)

    def test_stable_same_dna(self):
        appearance = build_appearance("kitten", "owner:42")
        self.assertEqual(render_svg(appearance), render_svg(appearance))

    def test_different_dna_changes_pixels_recipe(self):
        self.assertNotEqual(
            render_svg(build_appearance("kitten", "owner:42")),
            render_svg(build_appearance("kitten", "owner:43")),
        )

    def test_legacy_unsupported_species_rejected(self):
        with self.assertRaises(ValueError):
            render_svg(build_appearance("otter", "owner:42", asset_version=1))


if __name__ == "__main__":
    unittest.main()

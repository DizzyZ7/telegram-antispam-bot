"""Released recipe versions are frozen, regardless of the current default."""
import json
from hashlib import sha256
from pathlib import Path
import unittest

from fosgen_pets.core import Appearance
from fosgen_pets.render_svg import render_svg


class FrozenV2RecipeTests(unittest.TestCase):
    def test_reviewed_v2_recipes_remain_byte_stable(self):
        fixtures = json.loads(Path(__file__).with_name("fixtures").joinpath("fosgen_pets_v2.json").read_text())
        for fixture in fixtures:
            appearance = Appearance(**fixture["dna"])
            with self.subTest(species=appearance.species):
                self.assertEqual(appearance.fingerprint(), fixture["dna_sha256"])
                self.assertEqual(sha256(render_svg(appearance).encode()).hexdigest(), fixture["svg_sha256"])

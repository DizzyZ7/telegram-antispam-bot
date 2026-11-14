"""Historical fixtures prevent an upgrade from changing an existing pet."""
from dataclasses import replace
from hashlib import sha256
import unittest

from fosgen_pets.core import Appearance, MessageEligibility, build_appearance, eligible_message, validate_appearance
from fosgen_pets.render_svg import render_svg


class AppearanceVersionTests(unittest.TestCase):
    def test_historical_v1_dna_and_svg_are_byte_stable(self):
        fixtures = (
            ("kitten", "cream", "tabby", "d0ce2d764facde223ec5a36ef4e4f970c73b27f7484c8acad49f184084e1a2ad"),
            ("foal", "bay", "pinto", "0f541bf72078de48c1fe22c5a7c328e55cb9ad8ea1aa2fd4ca4220c2eb663086"),
            ("calf", "brown", "piebald", "cd3125561f69f4021932ee0e01abfc85879c5255793c310c0edba3461cca4dab"),
        )
        for species, palette, pattern, expected in fixtures:
            a = Appearance(species, palette, pattern, "amber", "heart", 911)
            self.assertNotIn("pattern_seed", a.canonical_dna())
            self.assertEqual(sha256(render_svg(a).encode()).hexdigest(), expected)

    def test_new_dna_contains_bounded_explicit_pattern_seed(self):
        a = build_appearance("kitten", "new-owner", pattern_seed=999999)
        self.assertEqual(a.asset_version, 2)
        self.assertEqual(a.pattern_seed, 999999)
        self.assertIn('"pattern_seed":999999', a.canonical_dna())
        self.assertEqual(a, build_appearance("kitten", "new-owner", pattern_seed=999999))
        self.assertNotEqual(a.fingerprint(), replace(a, pattern_seed=0).fingerprint())

    def test_invalid_numeric_types_and_ranges_are_rejected(self):
        for seed in (-1, 1000000, True, 1.0, "12"):
            with self.subTest(seed=seed), self.assertRaises(ValueError):
                build_appearance("kitten", "owner", pattern_seed=seed)
        for attempt in (-1, True, 1.5):
            with self.subTest(attempt=attempt), self.assertRaises(ValueError):
                build_appearance("kitten", "owner", attempt)

    def test_legacy_generator_available_for_recovery(self):
        a = build_appearance("foal", "owner:123", asset_version=1)
        self.assertEqual(a.asset_version, 1)
        self.assertIsNone(a.pattern_seed)

    def test_nonvisual_none_variant_is_canonicalized_for_new_pets(self):
        for seed in range(30):
            a = build_appearance("kitten", str(seed))
            if a.marking == "none":
                self.assertEqual(a.marking_variant, 0)

    def test_commands_and_nonwords_do_not_earn_a_pet(self):
        for text in ("/pet my progress", "  /start hello there", "1234 5678", "____ ____", "a " * 6000):
            self.assertFalse(eligible_message(MessageEligibility(1, -100, 1, text, opted_in=True)), text[:40])

    def test_v2_rejects_variant_aliases_that_would_render_identically(self):
        a = build_appearance("kitten", "canonical-variants")
        for marking, first_invalid in (("none", 1), ("sock_left", 9), ("sock_right", 9),
                                       ("freckle", 4), ("star", 35), ("heart", 35), ("blaze", 35)):
            with self.subTest(marking=marking), self.assertRaises(ValueError):
                validate_appearance(replace(a, marking=marking, marking_variant=first_invalid))


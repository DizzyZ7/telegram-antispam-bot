"""Offline unit tests for the opt-in Fosgen Pets foundation."""
import unittest

from fosgen_pets.core import (
    MessageEligibility, SPECIES, build_appearance, choose_species,
    eligible_message,
)


class PetCoreTests(unittest.TestCase):
    def test_all_species_have_valid_appearances(self):
        for species in SPECIES:
            appearance = build_appearance(species, "owner:123")
            self.assertEqual(appearance.species, species)
            self.assertEqual(len(appearance.fingerprint()), 64)

    def test_deterministic_across_restarts(self):
        a = build_appearance("foal", "owner:123")
        self.assertEqual(a.canonical_dna(), build_appearance("foal", "owner:123").canonical_dna())

    def test_retry_changes_candidate(self):
        a = build_appearance("kitten", "owner:123", 0)
        b = build_appearance("kitten", "owner:123", 1)
        self.assertNotEqual(a.fingerprint(), b.fingerprint())

    def test_unknown_species_rejected(self):
        with self.assertRaises(ValueError):
            build_appearance("dragon", "owner:123")

    def test_moderated_and_unconsented_messages_excluded(self):
        msg = MessageEligibility(1, -100, 42, "Hello lovely kitten", opted_in=True)
        self.assertTrue(eligible_message(msg))
        self.assertFalse(eligible_message(MessageEligibility(1, -100, 42, "Hello lovely kitten")))
        self.assertFalse(eligible_message(MessageEligibility(1, -100, 42, "Hello lovely kitten", opted_in=True, moderated=True)))
        self.assertFalse(eligible_message(MessageEligibility(1, -100, 42, "Hello lovely kitten", opted_in=True, is_bot=True)))

    def test_personality_mapping(self):
        self.assertEqual(choose_species({"calm": 10, "kindness": 9}), "calf")


if __name__ == "__main__":
    unittest.main()

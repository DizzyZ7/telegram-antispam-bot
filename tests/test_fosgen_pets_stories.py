"""Stories use immutable pet traits and never anyone's private messages."""
import unittest
from fosgen_pets.core import build_appearance


class OriginStoryTests(unittest.TestCase):
    def test_story_is_stable_and_has_real_variation(self):
        from fosgen_pets.stories import origin_story
        stories = []
        for seed in range(30):
            appearance = build_appearance("kitten", str(seed))
            story = origin_story(appearance)
            self.assertEqual(story, origin_story(appearance))
            self.assertTrue(80 <= len(story) <= 600)
            self.assertNotIn("ё", story)
            stories.append(story)
        self.assertGreater(len(set(stories)), 15)

    def test_every_species_has_a_russian_story_without_raw_owner_data(self):
        from fosgen_pets.core import SPECIES
        from fosgen_pets.stories import origin_story
        for species in SPECIES:
            story = origin_story(build_appearance(species, "private-test-value-never-show-this"))
            self.assertNotIn("private-test-value", story)
            self.assertNotIn(species, story)

    def test_new_stories_match_logical_sex_and_do_not_give_hooves_a_paw(self):
        from fosgen_pets.biography import pet_sex
        from fosgen_pets.stories import origin_story
        for species in ("calf", "foal", "fox", "alpaca"):
            for seed in range(30):
                appearance = build_appearance(species, str(seed), asset_version=3)
                text = origin_story(appearance)
                if pet_sex(appearance) == "female":
                    self.assertIn("Она ", text)
                    self.assertNotIn("Он ", text)
                else:
                    self.assertIn("Он ", text)
                if species in {"calf", "foal", "alpaca"}:
                    self.assertNotIn("лапу", text)

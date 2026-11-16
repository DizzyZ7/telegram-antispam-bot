"""Pure rules: calm progression, fixed clocks and anatomically plausible stories."""
from datetime import datetime, timezone, date
import unittest

from fosgen_pets.core import build_appearance, SPECIES


class WalkRulesTests(unittest.TestCase):
    def test_levels_have_explicit_thresholds_and_cap(self):
        from fosgen_pets.walks import level_for_xp
        self.assertEqual([level_for_xp(x) for x in (0, 20, 25, 99, 100, 224, 225, 10**9)],
                         [1, 1, 2, 2, 3, 3, 4, 100])
        for bad in (-1, True, 1.5, "20"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                level_for_xp(bad)

    def test_day_is_moscow_and_requires_aware_datetime(self):
        from fosgen_pets.walks import walk_day
        self.assertEqual(walk_day(datetime(2026, 11, 16, 20, 59, tzinfo=timezone.utc)), date(2026, 11, 16))
        self.assertEqual(walk_day(datetime(2026, 11, 16, 21, 0, tzinfo=timezone.utc)), date(2026, 11, 17))
        with self.assertRaises(ValueError):
            walk_day(datetime(2026, 11, 16))

    def test_all_species_and_routes_have_stable_bounded_sex_aware_activities(self):
        from fosgen_pets.walks import activity, ROUTES
        stories = set()
        for species in SPECIES:
            for route in ROUTES:
                for seed in range(4):
                    appearance = build_appearance(species, str(seed), asset_version=3)
                    text = activity(appearance, date(2026, 11, 16), route)
                    self.assertEqual(text, activity(appearance, date(2026, 11, 16), route))
                    self.assertTrue(10 <= len(text) <= 250)
                    self.assertNotIn("ё", text)
                    if species not in {"foal", "calf", "fawn", "capybara", "alpaca", "bunny"}:
                        self.assertNotIn("щипал", text)
                    stories.add(text)
        self.assertGreater(len(stories), 12)
        with self.assertRaises(ValueError):
            activity(build_appearance("fox", "x"), date(2026, 11, 16), "raw-private-input")

"""Sex is a permanent logical trait, not a guess from the owner's identity."""
import unittest

from fosgen_pets.core import build_appearance


class BiographyTests(unittest.TestCase):
    def test_sex_has_frozen_v1_vectors_and_does_not_modify_dna(self):
        from fosgen_pets.biography import SEX_VERSION, pet_sex
        self.assertEqual(SEX_VERSION, 1)
        # These expected values pin an independent SHA256 namespace specification.
        import hashlib
        for species, seed in (("kitten", "a"), ("calf", "495463"), ("foal", "b")):
            appearance = build_appearance(species, seed, asset_version=3)
            before = appearance.canonical_dna()
            expected = ("female" if hashlib.sha256(
                b"fosgen-pets-sex-v1:" + appearance.fingerprint().encode("ascii")
            ).digest()[0] & 1 else "male")
            self.assertEqual(pet_sex(appearance), expected)
            self.assertEqual(appearance.canonical_dna(), before)

    def test_both_sexes_are_present_without_a_species_bias(self):
        from fosgen_pets.biography import pet_sex, sex_label
        for species in ("calf", "foal", "kitten", "otter"):
            values = [pet_sex(build_appearance(species, f"sex-{n}", asset_version=3)) for n in range(200)]
            self.assertTrue(65 < values.count("female") < 135, (species, values.count("female")))
            self.assertIn(sex_label(build_appearance(species, "label")), ("мальчик ♂", "девочка ♀"))

    def test_frozen_public_v3_calf_vector(self):
        from fosgen_pets.core import Appearance
        from fosgen_pets.biography import pet_sex
        appearance = Appearance(asset_version=3, eyes="amber", marking="none", marking_variant=0,
            palette="brown", pattern="solid", pattern_seed=495463, species="calf")
        self.assertEqual(appearance.fingerprint(), "477907cd6d12937be42b28c2c60121f8b4d1834dc319a56d84be36576259fb16")
        self.assertEqual(pet_sex(appearance), "male")

    def test_literal_male_and_female_vectors_for_every_existing_dna_version(self):
        from fosgen_pets.biography import pet_sex
        vectors = (
            (1, 'frozen-sex-2', 'da5b02f07972b75d919a08869417ce90997d85e65ceeb9788f77b277cc860811', 'male'),
            (1, 'frozen-sex-0', '1d8e794666a0edda5bc09204eb4f2a2c10ffc99e5f15918232727ed80e04700a', 'female'),
            (2, 'frozen-sex-1', 'a9593c7c20ac273b126b84d7f70943372c8ca3192a42a5a96447303006da1bb4', 'male'),
            (2, 'frozen-sex-0', 'cdfe0bb4689f1446faae62013f9583a28909927ff52536d2a2963760afc2b6db', 'female'),
            (3, 'frozen-sex-0', 'bdc5dcb511fe6ab773df40698f40c390103b25c7f97232fa9008416c39b2d0e2', 'male'),
            (3, 'frozen-sex-1', 'a85188cfdb7bd53be53b608d2440dcc9cdf509d6c04bc9ce92073898bf60c946', 'female'),
        )
        for version, seed, fingerprint, expected in vectors:
            with self.subTest(version=version, expected=expected):
                appearance = build_appearance('kitten', seed, asset_version=version)
                self.assertEqual(appearance.fingerprint(), fingerprint)
                self.assertEqual(pet_sex(appearance), expected)

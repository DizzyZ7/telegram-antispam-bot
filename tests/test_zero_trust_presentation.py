import random
import unittest


class ZeroTrustPresentationTests(unittest.TestCase):
    def test_callback_round_trip_is_exact_and_bounded(self):
        from zero_trust.presentation import decode_callback, encode_callback

        payload = encode_callback(41, 777, 12)
        self.assertEqual(payload, "zt:41:777:12")
        self.assertEqual(decode_callback(payload), (41, 777, 12))
        self.assertLessEqual(len(payload.encode("utf-8")), 64)

    def test_malformed_or_overflow_shaped_callbacks_are_rejected(self):
        from zero_trust.presentation import decode_callback

        invalid = (
            "",
            "captcha:41:777:12",
            "zt:41:777",
            "zt:41:777:12:99",
            "zt:not-int:777:12",
            "zt:-1:777:12",
            "zt:41:-777:12",
            "zt:" + "9" * 80 + ":777:12",
        )
        for value in invalid:
            with self.subTest(value=value):
                self.assertIsNone(decode_callback(value))

    def test_prompt_has_four_unique_options_and_contains_answer(self):
        from zero_trust.presentation import generate_prompt

        for seed in range(20):
            with self.subTest(seed=seed):
                prompt = generate_prompt(random.Random(seed))
                self.assertEqual(len(prompt.options), 4)
                self.assertEqual(len(set(prompt.options)), 4)
                self.assertIn(prompt.answer, prompt.options)
                self.assertRegex(prompt.question, r"^\d+ \+ \d+ = \?$")

    def test_keyboard_payloads_include_exact_session_and_user(self):
        from zero_trust.models import CaptchaPrompt
        from zero_trust.presentation import build_challenge_keyboard

        prompt = CaptchaPrompt(question="4 + 8 = ?", answer=12, options=(12, 11, 13, 14))
        keyboard = build_challenge_keyboard(41, 777, prompt)
        payloads = [button.callback_data for row in keyboard.inline_keyboard for button in row]

        self.assertEqual(
            payloads,
            [
                "zt:41:777:12",
                "zt:41:777:11",
                "zt:41:777:13",
                "zt:41:777:14",
            ],
        )


if __name__ == "__main__":
    unittest.main()

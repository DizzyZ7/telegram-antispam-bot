"""Telegram-native channel links are valid in optional writers' links."""
from __future__ import annotations

import unittest

from writers_submission.models import ModerationDeliveryContext, ValidationError
from writers_submission.promo import render_owner_promo
from writers_submission.uploads import (
    normalize_extra_link, normalize_submission_details, validate_submission_fields,
)


def details(*links: str) -> dict[str, object]:
    return {
        "form_version": 3,
        "fandom": "Гарри Поттер",
        "size_category": "мини",
        "rating": "R",
        "completion": "в процессе",
        "visual_mode": "palette",
        "palette_colors": ["#112233", "#334455", "#556677", "#778899"],
        "main_genres": ["Романтика"],
        "warnings_present": "no",
        "warnings": [],
        "extra_links": list(links),
    }


def submission(*links: str):
    return validate_submission_fields(
        title="История в Хогвартсе",
        work_type="ФФ",
        genre="Гет",
        description="Сначала приходит письмо. Затем герои встречаются. Финал меняет все.",
        body_text="",
        external_url="https://ficbook.net/readfic/12345",
        has_ready_file=False,
        details=details(*links),
    )


class TelegramShorthandLinksTests(unittest.TestCase):
    def test_telegram_channel_username_accepted_and_expanded(self):
        examples = (
            ("@ff_info_ru", "https://t.me/ff_info_ru"),
            ("t.me/ff_info_ru", "https://t.me/ff_info_ru"),
            ("telegram.me/ff_info_ru", "https://telegram.me/ff_info_ru"),
            ("www.t.me/ff_info_ru", "https://www.t.me/ff_info_ru"),
            ("http://t.me/ff_info_ru", "https://t.me/ff_info_ru"),
            ("https://t.me/ff_info_ru", "https://t.me/ff_info_ru"),
            ("t.me/+validInvite123", "https://t.me/+validInvite123"),
            ("t.me/c/123456/78", "https://t.me/c/123456/78"),
            ("t.me/ff_info_ru/123?single", "https://t.me/ff_info_ru/123?single"),
        )
        for raw, wanted in examples:
            with self.subTest(raw=raw):
                self.assertEqual(normalize_extra_link(raw), wanted)
                normalized = normalize_submission_details(
                    details(raw), complete=True, work_type="ФФ"
                )
                self.assertEqual(normalized["extra_links"], [wanted])

    def test_drafts_keep_unfinished_input_and_submission_checks_final_links(self):
        raw = details("t.me/ff_info_ru", "@ff_info_ru", "https://example.com/post")
        draft = normalize_submission_details(raw, complete=False, work_type="ФФ")
        self.assertEqual(draft["extra_links"], raw["extra_links"])
        final = normalize_submission_details(raw, complete=True, work_type="ФФ")
        self.assertEqual(
            final["extra_links"],
            ["https://t.me/ff_info_ru", "https://example.com/post"],
        )

    def test_published_post_links_raw_shorthand_saved_in_sealed_revision(self):
        fields = submission("@ff_info_ru", "t.me/another_channel", "https://example.com/page")
        post = render_owner_promo(ModerationDeliveryContext(
            author_user_id=77,
            title=fields.title,
            work_type=fields.work_type,
            genre=fields.genre,
            description=fields.description,
            body_text="",
            external_url=fields.external_url,
            files=(),
            # In storage, the sealed revision keeps the author's raw draft
            # details_json rather than rewriting to normalized links.
            details=details("@ff_info_ru", "t.me/another_channel", "https://example.com/page"),
        ))
        self.assertIn('href="https://t.me/ff_info_ru"', post)
        self.assertIn('href="https://t.me/another_channel"', post)
        self.assertIn('href="https://example.com/page"', post)
        self.assertEqual(post.count("ТГ-канал"), 2)
        self.assertNotIn('href="@ff_info_ru"', post)
        self.assertNotIn('href="t.me/another_channel"', post)

    def test_invalid_or_unsafe_telegram_shorthand_is_rejected(self):
        examples = (
            "@bad", "@bad.name", "@user/name", "t.me", "t.me/",
            "t.me/@untrusted", "t.me/hello#fragment",
            "t.me/ff_info_ru\\evil", "t.me/ff_info_ru bad",
            "http://example.com/path", "javascript:alert(1)",
            "tg://resolve?domain=ff_info_ru", "//t.me/ff_info_ru",
            "https://user:pass@t.me/ff_info_ru",
            "https://t.me:99999/ff_info_ru",
        )
        for value in examples:
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    normalize_extra_link(value)
                with self.assertRaises(ValidationError):
                    normalize_submission_details(
                        details(value), complete=True, work_type="ФФ",
                    )

    def test_nontelegram_websites_still_require_https(self):
        self.assertEqual(normalize_extra_link("https://example.com/work"),
                         "https://example.com/work")
        self.assertEqual(normalize_extra_link("https://ficbook.net/readfic/1"),
                         "https://ficbook.net/readfic/1")
        with self.assertRaises(ValidationError):
            normalize_extra_link("example.com/work")
        with self.assertRaises(ValidationError):
            normalize_extra_link("http://example.com/work")

    def test_main_work_url_still_requires_full_https(self):
        for bad in ("@ff_info_ru", "t.me/ff_info_ru"):
            with self.subTest(value=bad), self.assertRaises(ValidationError):
                validate_submission_fields(
                    title="Title", work_type="Work", genre="Genre",
                    description="Description", body_text="Body",
                    external_url=bad, has_ready_file=False,
                )


if __name__ == "__main__":
    unittest.main()

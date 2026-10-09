from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from writers_submission.models import ValidationError
from writers_submission.uploads import (
    normalize_submission_details,
    validate_staged_file,
    validate_submission_fields,
)


def details(**updates):
    value = {
        "form_version": 2,
        "fandom": "Магическая академия",
        "size_category": "миди",
        "rating": "PG-13",
        "completion": "завершен",
        "size_words": 13000,
        "pages": 42,
        "parts": 7,
        "extra_links": ["https://t.me/example"],
        "visual_mode": "palette",
        "palette_colors": ["#AABBCC", "#FF0000", "#00FF00", "#0000FF"],
    }
    value.update(updates)
    return value


class FicbookFormValidationTests(unittest.TestCase):
    def test_link_only_submission_needs_no_manuscript_upload(self):
        result = validate_submission_fields(
            title="Пробуждение",
            work_type="ФФ",
            genre="Гет",
            description="Синопсис",
            body_text="",
            external_url="https://ficbook.net/readfic/1234",
            has_ready_file=False,
            details=details(),
        )
        self.assertEqual(result.body_text, "")
        self.assertEqual(result.details["palette_colors"], ["#AABBCC", "#FF0000", "#00FF00", "#0000FF"])

    def test_partial_v2_draft_accepts_empty_fields_but_legacy_does_not(self):
        base = dict(title="", work_type="", genre="", description="", body_text="",
                    external_url=None, has_ready_file=False, require_work_content=False)
        result = validate_submission_fields(**base, details={"form_version": 2})
        self.assertEqual(result.title, "")
        with self.assertRaises(ValidationError):
            validate_submission_fields(**base)

    def test_partial_ficbook_url_and_incomplete_extra_links_save_as_draft(self):
        for url in ("h", "https:", "https://ficbook.net/re", "not-an-url-yet"):
            with self.subTest(url=url):
                result = validate_submission_fields(
                    title="", work_type="", genre="", description="",
                    body_text="", external_url=url, has_ready_file=False,
                    details=details(extra_links=["https:", "t.me/author"]),
                    require_work_content=False,
                )
                self.assertEqual(result.external_url, url)
                self.assertEqual(result.details["extra_links"], ["https:", "t.me/author"])
        with self.assertRaisesRegex(ValidationError, "HTTPS"):
            validate_submission_fields(
                title="Название", work_type="Оридж", genre="Джен",
                description="Описание", body_text="", external_url="https:",
                has_ready_file=False, details=details(),
                require_work_content=True,
            )
        with self.assertRaises(ValidationError):
            normalize_submission_details(
                details(extra_links=["https:"]), complete=True
            )

    def test_palette_and_completion_are_normalized(self):
        result = normalize_submission_details(
            details(completion="в процессе", palette_colors=["#aabbcc", "#ff0000", "#00ff00", "#0000ff"]),
            complete=True,
        )
        self.assertEqual(result["size_words"], None)
        self.assertEqual(result["pages"], None)
        self.assertEqual(result["palette_colors"][0], "#AABBCC")

    def test_submit_rejects_incomplete_or_corrupt_metadata(self):
        cases = [
            details(palette_colors=["#000000"]),
            details(size_category="гигант"),
            details(rating=""),
            details(extra_links=["javascript:alert(1)"]),
            details(extra_links=["http://t.me/test"]),
            details(palette_colors=["#12345Z"] * 4),
            details(pages=0),
            details(size_words="lots"),
            details(visual_mode="image", extra_links=["https://user:pass@site.test/"]),
        ]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValidationError):
                normalize_submission_details(case, complete=True)

    def test_ikf_cover_template_is_stored_and_whitelisted(self):
        for template in ("classic", "ribbon", "contrast", "minimal"):
            with self.subTest(template=template):
                result = normalize_submission_details(details(cover_template=template), complete=True)
                self.assertEqual(result["cover_template"], template)
        self.assertEqual(
            normalize_submission_details(details(), complete=True)["cover_template"],
            "classic",
        )
        with self.assertRaises(ValidationError):
            normalize_submission_details(details(cover_template="../private"), complete=True)

    def test_image_mode_clears_palette_without_erasing_other_options(self):
        result = normalize_submission_details(details(visual_mode="image"), complete=True)
        self.assertEqual(result["palette_colors"], [])
        self.assertEqual(result["rating"], "PG-13")


class IllustrationUploadValidationTests(unittest.TestCase):
    def test_png_and_jpeg_have_matching_magic_mime_and_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            png = root / "palette.png"
            png.write_bytes(
                bytes.fromhex("89504e470d0a1a0a")
                + bytes.fromhex("0000000d494844520000000100000001")
                + b"payload"
            )
            jpg = root / "cover.jpg"
            jpg.write_bytes(bytes.fromhex("ffd8ff") + b"test" + bytes.fromhex("ffd9"))
            for path, mime, expected in (
                (png, "image/png", "png"),
                (jpg, "image/jpeg", "jpeg"),
            ):
                with self.subTest(path=path):
                    result = validate_staged_file(
                        path, filename=path.name, declared_mime=mime, max_bytes=1000
                    )
                    self.assertEqual(result.file_class, expected)
                    with self.assertRaises(ValidationError):
                        validate_staged_file(
                            path, filename=path.name, declared_mime="text/plain", max_bytes=1000
                        )

    def test_fake_png_fails_magic_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "fake.png"
            file.write_bytes(b"not-a-real-png")
            with self.assertRaises(ValidationError):
                validate_staged_file(
                    file, filename="fake.png", declared_mime="image/png", max_bytes=1000
                )


if __name__ == "__main__":
    unittest.main()

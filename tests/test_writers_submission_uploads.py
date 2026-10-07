from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from writers_submission.models import ValidationError
from writers_submission.uploads import (
    BODY_MAX,
    DESCRIPTION_MAX,
    EXTERNAL_URL_MAX,
    GENRE_MAX,
    TITLE_MAX,
    WORK_TYPE_MAX,
    normalize_display_filename,
    validate_staged_file,
    validate_submission_fields,
)


class SubmissionFieldValidationTests(unittest.TestCase):
    def test_required_metadata_and_payload_are_enforced(self):
        base = dict(
            title="Название",
            work_type="Рассказ",
            genre="Фантастика",
            description="Короткое описание",
            body_text="Текст",
            external_url=None,
            has_ready_file=False,
        )
        for field in ("title", "work_type", "genre", "description"):
            payload = dict(base)
            payload[field] = "   "
            with self.subTest(field=field), self.assertRaises(ValidationError):
                validate_submission_fields(**payload)

        payload = dict(base)
        payload["body_text"] = "  "
        with self.assertRaisesRegex(ValidationError, "payload"):
            validate_submission_fields(**payload)

        payload["has_ready_file"] = True
        normalized = validate_submission_fields(**payload)
        self.assertEqual(normalized.body_text, "")

    def test_metadata_is_normalized_without_destroying_body_formatting(self):
        result = validate_submission_fields(
            title="  Мой   рассказ  ",
            work_type="  Рассказ  ",
            genre="  Научная   фантастика ",
            description="  Описание   в  двух словах ",
            body_text="\n  Первая строка\n\n    Вторая с отступом\n",
            external_url=" https://example.org/work ",
            has_ready_file=False,
        )
        self.assertEqual(result.title, "Мой рассказ")
        self.assertEqual(result.work_type, "Рассказ")
        self.assertEqual(result.genre, "Научная фантастика")
        self.assertEqual(result.description, "Описание в двух словах")
        self.assertEqual(
            result.body_text,
            "Первая строка\n\n    Вторая с отступом",
        )
        self.assertEqual(result.external_url, "https://example.org/work")

    def test_exact_text_limits_pass_and_max_plus_one_fails(self):
        cases = (
            ("title", TITLE_MAX),
            ("work_type", WORK_TYPE_MAX),
            ("genre", GENRE_MAX),
            ("description", DESCRIPTION_MAX),
            ("body_text", BODY_MAX),
        )
        base = dict(
            title="t",
            work_type="w",
            genre="g",
            description="d",
            body_text="b",
            external_url=None,
            has_ready_file=False,
        )
        for field, limit in cases:
            allowed = dict(base)
            allowed[field] = "x" * limit
            validate_submission_fields(**allowed)

            rejected = dict(base)
            rejected[field] = "x" * (limit + 1)
            with self.subTest(field=field), self.assertRaises(ValidationError):
                validate_submission_fields(**rejected)

        allowed_url = "https://e.test/" + "a" * (EXTERNAL_URL_MAX - len("https://e.test/"))
        validate_submission_fields(**(base | {"external_url": allowed_url}))
        rejected_url = allowed_url + "a"
        with self.assertRaises(ValidationError):
            validate_submission_fields(**(base | {"external_url": rejected_url}))

    def test_only_https_external_links_are_accepted(self):
        base = dict(
            title="t",
            work_type="w",
            genre="g",
            description="d",
            body_text="b",
            has_ready_file=False,
        )
        for value in (
            "http://example.org/work",
            "ftp://example.org/work",
            "javascript:alert(1)",
            "https://",
            "https://user:pass@example.org/work",
        ):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                validate_submission_fields(**base, external_url=value)

        ok = validate_submission_fields(
            **base,
            external_url="https://example.org/work?q=1",
        )
        self.assertEqual(ok.external_url, "https://example.org/work?q=1")


class UploadValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _write(self, name: str, content: bytes) -> Path:
        path = self.root / name
        path.write_bytes(content)
        return path

    def test_pdf_signature_mime_and_extension_must_agree(self):
        path = self._write("work.pdf", b"%PDF-1.7\ncontent")
        result = validate_staged_file(
            path,
            filename="work.pdf",
            declared_mime="application/pdf",
            max_bytes=100,
        )
        self.assertEqual(result.file_class, "pdf")
        self.assertEqual(result.safe_filename, "work.pdf")
        self.assertEqual(result.byte_size, path.stat().st_size)
        self.assertEqual(len(result.sha256), 64)

        with self.assertRaises(ValidationError):
            validate_staged_file(
                path,
                filename="work.txt",
                declared_mime="text/plain",
                max_bytes=100,
            )

    def test_utf8_txt_passes_and_binary_txt_is_rejected(self):
        text_path = self._write("work.txt", "Привет\nмир".encode("utf-8"))
        result = validate_staged_file(
            text_path,
            filename="work.txt",
            declared_mime="text/plain; charset=utf-8",
            max_bytes=100,
        )
        self.assertEqual(result.file_class, "txt")

        binary = self._write("bad.txt", b"hello\x00world")
        with self.assertRaises(ValidationError):
            validate_staged_file(
                binary,
                filename="bad.txt",
                declared_mime="text/plain",
                max_bytes=100,
            )

    def test_docx_requires_bounded_zip_structure_without_extraction(self):
        path = self.root / "work.docx"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("word/document.xml", "<document/>")
        result = validate_staged_file(
            path,
            filename="work.docx",
            declared_mime=(
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document"
            ),
            max_bytes=10_000,
        )
        self.assertEqual(result.file_class, "docx")

        fake = self.root / "fake.docx"
        with zipfile.ZipFile(fake, "w") as archive:
            archive.writestr("payload.bin", b"x")
        with self.assertRaises(ValidationError):
            validate_staged_file(
                fake,
                filename="fake.docx",
                declared_mime=(
                    "application/vnd.openxmlformats-officedocument."
                    "wordprocessingml.document"
                ),
                max_bytes=10_000,
            )

    def test_docx_with_excessive_entry_count_is_rejected(self):
        path = self.root / "bomb.docx"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("word/document.xml", "<document/>")
            for index in range(260):
                archive.writestr(f"word/media/{index}.bin", b"")
        with self.assertRaisesRegex(ValidationError, "DOCX"):
            validate_staged_file(
                path,
                filename="bomb.docx",
                declared_mime=(
                    "application/vnd.openxmlformats-officedocument."
                    "wordprocessingml.document"
                ),
                max_bytes=100_000,
            )

    def test_oversize_file_is_rejected(self):
        path = self._write("work.txt", b"123456")
        with self.assertRaisesRegex(ValidationError, "size"):
            validate_staged_file(
                path,
                filename="work.txt",
                declared_mime="text/plain",
                max_bytes=5,
            )

    def test_display_filename_is_basename_only_and_rejects_nul(self):
        self.assertEqual(
            normalize_display_filename("../../draft/work.pdf"),
            "work.pdf",
        )
        self.assertEqual(
            normalize_display_filename(r"..\\draft\\work.pdf"),
            "work.pdf",
        )
        with self.assertRaises(ValidationError):
            normalize_display_filename("bad\x00name.pdf")


if __name__ == "__main__":
    unittest.main()

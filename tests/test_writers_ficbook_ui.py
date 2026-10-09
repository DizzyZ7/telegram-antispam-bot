"""Static smoke contracts for the Telegram Mini App dynamic Ficbook form."""

from __future__ import annotations

import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "writers_submission" / "static"


class FormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.groups = set()

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if attributes.get("id"):
            self.ids.add(attributes["id"])
        if attributes.get("data-choice-group"):
            self.groups.add(attributes["data-choice-group"])


class FicbookFormUiTests(unittest.TestCase):
    def test_form_has_required_dynamic_input_blocks(self):
        parser = FormParser()
        parser.feed((ROOT / "index.html").read_text(encoding="utf-8"))
        self.assertTrue({
            "titleInput", "fandomInput", "directionInput", "ratingInput",
            "sizeWordsInput", "pagesInput", "partsInput", "urlInput",
            "extraLinksInput", "descriptionInput", "paletteEditors",
            "palettePreview", "imageInput", "imagePreview",
            "saveButton", "submitButton",
        } <= parser.ids)
        self.assertEqual(parser.groups, {
            "workType", "sizeCategory", "completion", "visualMode",
        })

    def test_photo_upload_picker_supports_mobile_photos_and_cache_busting(self):
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "app.js").read_text(encoding="utf-8")
        self.assertIn("image/*,.png,.jpg,.jpeg,.webp,.heic,.heif", html)
        self.assertIn("app.js?v=draft-safe-v2", html)
        self.assertIn("prepareUploadFile", script)
        self.assertIn("await autosave({ immediate: true })", script)
        self.assertIn('setSaveState("Загружаю файлы…")', script)
        self.assertIn('state.savePromise', script)
        self.assertIn('if (uploaded) {', script)

    def test_javascript_has_reversible_choices_and_persisted_palette(self):
        js = (ROOT / "app.js").read_text(encoding="utf-8")
        self.assertIn('if (other !== input) { other.checked = false; }', js)
        self.assertIn('palette_colors:', js)
        self.assertIn('extra_links:', js)
        self.assertIn('renderPalette();', js)
        self.assertIn('validateReadyForm();', js)


if __name__ == "__main__":
    unittest.main()

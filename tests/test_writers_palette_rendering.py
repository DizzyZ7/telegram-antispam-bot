"""Real IKF RGB mockup rendering, including Pillow/font-less fallback."""
from __future__ import annotations

import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from writers_submission.promo import (
    render_palette_png,
    render_palette_fallback_png,
    render_custom_cover_png,
)

COLORS = ["#5B67F1", "#EF86AC", "#78CFBC", "#FFC777"]
TITLE = "я люблю пуушукппу"


class PaletteRenderingTests(unittest.TestCase):
    def _load(self, data):
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
        with Image.open(BytesIO(data)) as image:
            image.verify()
        with Image.open(BytesIO(data)) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.size, (1200, 450))
            self.assertEqual(image.mode, "RGB")
            return image.copy()

    def test_original_render_handles_real_moderation_sample_with_cyrillic(self):
        data = render_palette_png(COLORS, title=TITLE)
        image = self._load(data)
        self.assertGreater(len(data), 1000)
        # The primary mockup blends all four supplied colors.
        colors_in_image = {pixel for pixel in image.getdata()}
        for sample in COLORS:
            self.assertIn(tuple(bytes.fromhex(sample[1:])), colors_in_image)

    def test_missing_system_dejavu_font_uses_bundled_pillow_font(self):
        with patch.object(Path, "is_file", return_value=False):
            data = render_palette_png(COLORS, title=TITLE)
        self._load(data)

    def test_standard_library_fallback_has_exact_rgb_swatches(self):
        image = self._load(render_palette_fallback_png(COLORS))
        for x, color in zip((140, 440, 740, 1040), COLORS):
            with self.subTest(color=color):
                self.assertEqual(image.getpixel((x, 225)), tuple(bytes.fromhex(color[1:])))

    def test_malformed_palette_is_not_silently_rendered(self):
        for colors in ([], COLORS[:3], COLORS[:3] + ["red"], COLORS + ["#123456"]):
            with self.subTest(colors=colors):
                with self.assertRaises(ValueError):
                    render_palette_fallback_png(colors)
                with self.assertRaises(ValueError):
                    render_palette_png(colors, title=TITLE)


if __name__ == "__main__":
    unittest.main()

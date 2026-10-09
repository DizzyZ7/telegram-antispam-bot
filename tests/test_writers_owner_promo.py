from __future__ import annotations

import struct
import unittest
from io import BytesIO
from PIL import Image

from writers_submission.promo import render_owner_promo, render_palette_png, render_custom_cover_png
from writers_submission.models import ModerationDeliveryContext


class OwnerPreviewFormattingTests(unittest.TestCase):
    def context(self, **values):
        defaults = {
            "author_user_id": 77,
            "title": "Однажды в Лост-Крике",
            "work_type": "Оридж",
            "genre": "Джен",
            "description": "Детективная история о двух одиночествах",
            "body_text": "",
            "external_url": "https://ficbook.net/readfic/123",
            "files": (),
            "details": {
                "form_version": 2, "size_category": "макси",
                "rating": "R", "completion": "в процессе",
                "characters": "Джонатан Кросс/Лиам Миллер",
                "notes": "История в Аризоне",
                "visual_mode": "palette",
                "palette_colors": ["#112233", "#445566", "#778899", "#AABBCC"],
                "extra_links": ["https://t.me/rayskay_gavan"],
            },
        }
        defaults.update(values)
        return ModerationDeliveryContext(**defaults)

    def test_original_work_matches_ikf_post_structure(self):
        post = render_owner_promo(self.context())
        self.assertIn("<b>Однажды в Лост-Крике</b>", post)
        self.assertIn("#Ориджиналы | #Макси | #Джен | #R | #ВПроцессе", post)
        self.assertIn("Основные персонажи", post)
        self.assertIn("Примечания:", post)
        self.assertIn('<a href="https://ficbook.net/readfic/123">Читать на Фикбуке</a>', post)
        self.assertIn('<a href="https://t.me/rayskay_gavan">ТГ-канал</a>', post)

    def test_fanfic_escapes_untrusted_markup_and_hashtags(self):
        context = self.context(
            title="<Война & мир>",
            work_type="ФФ",
            genre="Слэш",
            description="<script>не выполнять</script>",
            details={**self.context().details,
                     "fandom": "Атака Титанов", "rating": "NC-17",
                     "completion": "завершен",
                     "size_words": 44000, "pages": 89, "parts": 15},
        )
        post = render_owner_promo(context)
        self.assertIn("&lt;Война &amp; мир&gt;", post)
        self.assertNotIn("<script>", post)
        self.assertIn("#АтакаТитанов", post)
        self.assertIn("#NC17", post)
        self.assertIn("89 страниц, 15 частей", post)

    def test_one_fixed_rough_palette_shows_four_colors_and_lower_right_title(self):
        colors = ["#8797A9", "#0F1A2B", "#3B4A61", "#526C82"]
        cover = render_palette_png(colors, title="Ничто не может нас спасти")
        with Image.open(BytesIO(cover)) as img:
            self.assertEqual(img.size, (1200, 450))
            self.assertEqual(img.mode, "RGB")
            self.assertEqual(img.getpixel((120, 30)), (135, 151, 169))
            self.assertGreater(len(set(img.getdata())), 4)
            lower_right = img.crop((460, 300, 1170, 440))
            self.assertTrue(any(
                pixel == (245, 243, 237) or pixel == (22, 25, 35)
                for pixel in lower_right.getdata()
            ))
        self.assertEqual(
            cover, render_palette_png(colors, title="Ничто не может нас спасти")
        )

    def test_paid_photo_cover_keeps_title_in_lower_right(self):
        original = Image.new("RGB", (500, 500), (240, 80, 120))
        with BytesIO() as tmp:
            original.save(tmp, format="JPEG")
            png = render_custom_cover_png(tmp.getvalue(), "Черная петля")
        with Image.open(BytesIO(png)) as rendered:
            self.assertEqual(rendered.size, (1200, 450))
            self.assertEqual(rendered.format, "PNG")
            self.assertTrue(any(
                pixel == (245, 243, 237)
                for pixel in rendered.crop((500, 310, 1170, 440)).getdata()
            ))

    def test_photo_cover_rejects_corrupt_image(self):
        with self.assertRaises(ValueError):
            render_custom_cover_png(b"not an image", "Название")

    def test_palette_is_valid_png_with_expected_dimensions(self):
        data = render_palette_png(["#112233", "#445566", "#778899", "#AABBCC"])
        self.assertTrue(data.startswith(bytes.fromhex("89504e470d0a1a0a")))
        self.assertEqual(data[12:16], b"IHDR")
        self.assertEqual(struct.unpack(">II", data[16:24]), (1200, 450))
        self.assertIn(b"IDAT", data)
        with self.assertRaisesRegex(ValueError, "four RGB"):
            render_palette_png(["#000000"])


if __name__ == "__main__":
    unittest.main()

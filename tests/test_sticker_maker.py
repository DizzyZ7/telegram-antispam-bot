"""DM isolation, reusable user packs, and real FFmpeg conversion regressions."""

import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from PIL import Image, ImageDraw

from sticker_maker.converter import (
    MAX_SOURCE_BYTES, MAX_STICKER_BYTES,
    StickerConversionError, convert_to_sticker,
)
from sticker_maker.handlers import (
    StickerState, media_file, pack_name, private, save_to_personal_pack,
)


class StickerFeatureTests(unittest.TestCase):
    def test_private_only(self):
        user = SimpleNamespace(id=123, is_bot=False)
        self.assertTrue(private(SimpleNamespace(chat=SimpleNamespace(type="private"), from_user=user)))
        self.assertFalse(private(SimpleNamespace(chat=SimpleNamespace(type="supergroup"), from_user=user)))
        self.assertFalse(private(SimpleNamespace(chat=SimpleNamespace(type="private"), from_user=SimpleNamespace(is_bot=True))))

    def test_gif_and_animation_only(self):
        document = SimpleNamespace(file_name="pic.gif", mime_type="application/octet-stream")
        self.assertIs(media_file(SimpleNamespace(animation=None, document=document)), document)
        self.assertIsNone(media_file(SimpleNamespace(animation=None, document=SimpleNamespace(file_name="file.pdf", mime_type="application/pdf"))))
        mp4_animation = SimpleNamespace(file_id="xyz")
        self.assertIs(media_file(SimpleNamespace(animation=mp4_animation, document=None)), mp4_animation)

    def test_pack_scoped_to_user(self):
        self.assertEqual(pack_name(123, "Fosgen_AntiSpam_bot"), "fosgen_123_by_Fosgen_AntiSpam_bot")
        self.assertEqual(pack_name(123, "Fosgen_AntiSpam_bot", 2), "fosgen_123_2_by_Fosgen_AntiSpam_bot")

    def test_pending_expiration_and_limits(self):
        state = StickerState()
        state.expect(123)
        self.assertTrue(state.waiting(123))
        state.pending[123] = 0
        self.assertFalse(state.waiting(123))
        self.assertIsNone(state.start(123))
        self.assertIn("Уже", state.start(123))
        state.finish(123)
        self.assertIn("20", state.start(123))


class StickerAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_then_append_to_same_user_set(self):
        from aiogram.exceptions import TelegramBadRequest
        bot = SimpleNamespace(
            get_me=AsyncMock(return_value=SimpleNamespace(username="Fosgen_AntiSpam_bot")),
            get_sticker_set=AsyncMock(side_effect=TelegramBadRequest(
                method=SimpleNamespace(__api_method__="getStickerSet"),
                message="Bad Request: STICKERSET_INVALID",
            )),
            create_new_sticker_set=AsyncMock(return_value=True),
            add_sticker_to_set=AsyncMock(return_value=True),
        )
        name = await save_to_personal_pack(bot, 123, b"webm")
        self.assertEqual(name, "fosgen_123_by_Fosgen_AntiSpam_bot")
        self.assertEqual(bot.create_new_sticker_set.await_args.kwargs["user_id"], 123)
        bot.get_sticker_set = AsyncMock(return_value=SimpleNamespace(stickers=[object()]))
        await save_to_personal_pack(bot, 123, b"webm")
        bot.add_sticker_to_set.assert_awaited_once()

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg unavailable in test environment")
    async def test_real_animated_gif_to_webm(self):
        with tempfile.TemporaryDirectory() as d:
            source, dest = Path(d) / "test.gif", Path(d) / "test.webm"
            images = []
            for x in range(12):
                frame = Image.new("RGBA", (180, 100), (0, 0, 0, 0))
                ImageDraw.Draw(frame).ellipse((x * 8, 20, x * 8 + 40, 60), fill="red")
                images.append(frame)
            images[0].save(source, save_all=True, append_images=images[1:], duration=90, loop=0, disposal=2)
            output = await convert_to_sticker(source, dest)
            self.assertEqual(output[:4], bytes((0x1A, 0x45, 0xDF, 0xA3)))
            self.assertLessEqual(len(output), MAX_STICKER_BYTES)
            self.assertEqual(output, dest.read_bytes())

    async def test_rejects_oversized_input(self):
        with tempfile.TemporaryDirectory() as d:
            source, dest = Path(d) / "large.gif", Path(d) / "out.webm"
            with source.open("wb") as stream:
                stream.truncate(MAX_SOURCE_BYTES + 1)
            with self.assertRaises(StickerConversionError):
                await convert_to_sticker(source, dest)


if __name__ == "__main__":
    unittest.main()

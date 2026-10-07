from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.types import MenuButtonWebApp

from writers_submission.menu import install_writers_submission_menu


class WritersSubmissionMenuTests(unittest.IsolatedAsyncioTestCase):
    async def test_installs_global_webapp_button_at_composer(self):
        bot = SimpleNamespace(set_chat_menu_button=AsyncMock(return_value=True))

        await install_writers_submission_menu(
            bot,
            public_url="https://example.test/writers/",
        )

        bot.set_chat_menu_button.assert_awaited_once()
        kwargs = bot.set_chat_menu_button.await_args.kwargs
        self.assertNotIn("chat_id", kwargs)
        button = kwargs["menu_button"]
        self.assertIsInstance(button, MenuButtonWebApp)
        self.assertEqual(button.text, "✒️ Отправить заявку")
        self.assertEqual(button.web_app.url, "https://example.test/writers/")

    async def test_rejects_false_telegram_api_result(self):
        bot = SimpleNamespace(set_chat_menu_button=AsyncMock(return_value=False))
        with self.assertRaisesRegex(RuntimeError, "menu button"):
            await install_writers_submission_menu(
                bot,
                public_url="https://example.test/writers/",
            )


if __name__ == "__main__":
    unittest.main()

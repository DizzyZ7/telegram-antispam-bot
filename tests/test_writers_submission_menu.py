from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.types import MenuButtonWebApp

from writers_submission.menu import install_writers_submission_menu
from writers_submission.runtime import start_writers_submission_runtime


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


    async def test_runtime_installs_menu_only_after_worker_started(self):
        events = []
        bot = SimpleNamespace(set_chat_menu_button=AsyncMock(
            side_effect=lambda **kwargs: events.append("menu") or True
        ))
        storage = SimpleNamespace(
            initialize=AsyncMock(),
            close=AsyncMock(),
        )
        web_server = SimpleNamespace(
            start=AsyncMock(side_effect=lambda: events.append("web")),
            stop=AsyncMock(),
        )
        worker = SimpleNamespace(
            start=AsyncMock(side_effect=lambda: events.append("worker")),
            stop=AsyncMock(),
        )
        config = SimpleNamespace(
            enabled=True,
            writers_chat_id=-100123,
            session_ttl_seconds=600,
            public_url="https://example.test/writers/",
            bind_host="0.0.0.0",
            port=3000,
        )
        with (
            patch("writers_submission.runtime.PostgresWritersSubmissionStorage", return_value=storage),
            patch("writers_submission.runtime.WritersSubmissionService"),
            patch("writers_submission.runtime.WritersFileService"),
            patch("writers_submission.runtime.SessionSigner"),
            patch("writers_submission.runtime.create_writers_web_app"),
            patch("writers_submission.runtime.WritersWebServer", return_value=web_server),
            patch("writers_submission.runtime.WritersDeliveryWorker", return_value=worker),
            patch("writers_submission.runtime.register_writers_submission_handlers"),
        ):
            runtime = await start_writers_submission_runtime(
                SimpleNamespace(bot=bot, dp=object()),
                config,
                database_url="postgresql://test:test@localhost/db",
                bot_token="test-token",
            )
        self.assertEqual(events, ["web", "worker", "menu"])
        await runtime.stop()

    async def test_menu_network_failure_preserves_healthy_runtime(self):
        bot = SimpleNamespace(set_chat_menu_button=AsyncMock(
            side_effect=RuntimeError("Telegram temporarily unavailable")
        ))
        storage = SimpleNamespace(initialize=AsyncMock(), close=AsyncMock())
        web_server = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
        worker = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
        config = SimpleNamespace(
            enabled=True,
            writers_chat_id=-100123,
            session_ttl_seconds=600,
            public_url="https://example.test/writers/",
            bind_host="0.0.0.0",
            port=3000,
        )
        with (
            patch("writers_submission.runtime.PostgresWritersSubmissionStorage", return_value=storage),
            patch("writers_submission.runtime.WritersSubmissionService"),
            patch("writers_submission.runtime.WritersFileService"),
            patch("writers_submission.runtime.SessionSigner"),
            patch("writers_submission.runtime.create_writers_web_app"),
            patch("writers_submission.runtime.WritersWebServer", return_value=web_server),
            patch("writers_submission.runtime.WritersDeliveryWorker", return_value=worker),
            patch("writers_submission.runtime.register_writers_submission_handlers"),
        ):
            with self.assertLogs("writers_submission.runtime", level="ERROR"):
                runtime = await start_writers_submission_runtime(
                    SimpleNamespace(bot=bot, dp=object()),
                    config,
                    database_url="postgresql://test:test@localhost/db",
                    bot_token="test-token",
                )
        worker.start.assert_awaited_once()
        await runtime.stop()
        storage.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()

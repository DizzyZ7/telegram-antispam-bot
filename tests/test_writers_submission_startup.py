from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from writers_submission.runtime import (
    TelegramWritersEligibilityChecker,
    start_writers_submission_runtime,
)


class TelegramWritersEligibilityCheckerTests(unittest.IsolatedAsyncioTestCase):
    async def test_accepts_only_current_normal_membership_states(self):
        bot = SimpleNamespace(get_chat_member=AsyncMock())
        checker = TelegramWritersEligibilityChecker(bot, -1002619489118)

        for status in ("member", "administrator", "creator"):
            with self.subTest(status=status):
                bot.get_chat_member.reset_mock()
                bot.get_chat_member.return_value = SimpleNamespace(
                    status=status,
                    is_member=True,
                )
                self.assertTrue(await checker(77))
                bot.get_chat_member.assert_awaited_once_with(
                    -1002619489118,
                    77,
                )

    async def test_rejects_left_kicked_restricted_and_bot_api_errors(self):
        bot = SimpleNamespace(get_chat_member=AsyncMock())
        checker = TelegramWritersEligibilityChecker(bot, -1002619489118)

        for status, is_member in (
            ("left", False),
            ("kicked", False),
            ("restricted", False),
            ("restricted", True),
        ):
            with self.subTest(status=status, is_member=is_member):
                bot.get_chat_member.return_value = SimpleNamespace(
                    status=status,
                    is_member=is_member,
                )
                self.assertFalse(await checker(77))

        bot.get_chat_member.side_effect = RuntimeError("Telegram unavailable")
        self.assertFalse(await checker(77))


class WritersSubmissionRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def config(self, *, enabled=True):
        return SimpleNamespace(
            enabled=enabled,
            writers_chat_id=-1002619489118,
            file_chat_id=-100222,
            moderation_chat_id=-100111,
            moderator_ids=frozenset({9001}),
            public_url="https://example.test/writers/",
            session_ttl_seconds=43200,
            bind_host="0.0.0.0",
            port=8080,
            max_file_bytes=20 * 1024 * 1024,
            max_files=3,
            rate_limit_window_seconds=60,
            init_data_max_age_seconds=900,
        )

    async def test_disabled_mode_constructs_no_resources_or_handlers(self):
        app = SimpleNamespace(bot=object(), dp=object())
        with (
            patch("writers_submission.runtime.PostgresWritersSubmissionStorage") as storage,
            patch("writers_submission.runtime.create_writers_web_app") as create_web,
            patch("writers_submission.runtime.register_writers_submission_handlers") as handlers,
        ):
            runtime = await start_writers_submission_runtime(
                app,
                self.config(enabled=False),
                database_url=None,
                bot_token=None,
            )

        self.assertIsNone(runtime)
        storage.assert_not_called()
        create_web.assert_not_called()
        handlers.assert_not_called()

    async def test_enabled_start_initializes_storage_before_web_handlers_and_worker(self):
        order = []
        storage = SimpleNamespace(
            initialize=AsyncMock(side_effect=lambda: order.append("storage.initialize")),
            close=AsyncMock(side_effect=lambda: order.append("storage.close")),
        )
        web_server = SimpleNamespace(
            start=AsyncMock(side_effect=lambda: order.append("web.start")),
            stop=AsyncMock(side_effect=lambda: order.append("web.stop")),
        )
        worker = SimpleNamespace(
            start=AsyncMock(side_effect=lambda: order.append("worker.start")),
            stop=AsyncMock(side_effect=lambda: order.append("worker.stop")),
        )
        app = SimpleNamespace(bot=object(), dp=object())

        with (
            patch(
                "writers_submission.runtime.PostgresWritersSubmissionStorage",
                return_value=storage,
            ),
            patch("writers_submission.runtime.WritersSubmissionService"),
            patch("writers_submission.runtime.WritersFileService"),
            patch("writers_submission.runtime.SessionSigner"),
            patch("writers_submission.runtime.create_writers_web_app", return_value=object()),
            patch(
                "writers_submission.runtime.WritersWebServer",
                return_value=web_server,
            ),
            patch(
                "writers_submission.runtime.WritersDeliveryWorker",
                return_value=worker,
            ),
            patch(
                "writers_submission.runtime.register_writers_submission_handlers",
                side_effect=lambda *args, **kwargs: order.append("handlers"),
            ),
        ):
            runtime = await start_writers_submission_runtime(
                app,
                self.config(),
                database_url="postgresql://user:pass@db/name",
                bot_token="token",
            )

        self.assertIsNotNone(runtime)
        self.assertEqual(
            order[:4],
            [
                "storage.initialize",
                "web.start",
                "handlers",
                "worker.start",
            ],
        )
        await runtime.stop()
        self.assertEqual(
            order[-3:],
            ["worker.stop", "web.stop", "storage.close"],
        )

    async def test_web_bind_failure_cleans_storage_and_never_registers_handlers_or_worker(self):
        storage = SimpleNamespace(
            initialize=AsyncMock(),
            close=AsyncMock(),
        )
        web_server = SimpleNamespace(
            start=AsyncMock(side_effect=OSError("address already in use")),
            stop=AsyncMock(),
        )
        worker = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
        app = SimpleNamespace(bot=object(), dp=object())

        with (
            patch(
                "writers_submission.runtime.PostgresWritersSubmissionStorage",
                return_value=storage,
            ),
            patch("writers_submission.runtime.WritersSubmissionService"),
            patch("writers_submission.runtime.WritersFileService"),
            patch("writers_submission.runtime.SessionSigner"),
            patch("writers_submission.runtime.create_writers_web_app", return_value=object()),
            patch(
                "writers_submission.runtime.WritersWebServer",
                return_value=web_server,
            ),
            patch(
                "writers_submission.runtime.WritersDeliveryWorker",
                return_value=worker,
            ),
            patch(
                "writers_submission.runtime.register_writers_submission_handlers"
            ) as handlers,
        ):
            with self.assertRaisesRegex(OSError, "address already"):
                await start_writers_submission_runtime(
                    app,
                    self.config(),
                    database_url="postgresql://user:pass@db/name",
                    bot_token="token",
                )

        storage.initialize.assert_awaited_once()
        web_server.start.assert_awaited_once()
        handlers.assert_not_called()
        worker.start.assert_not_awaited()
        web_server.stop.assert_awaited_once()
        storage.close.assert_awaited_once()

    async def test_partial_worker_failure_stops_web_and_storage_once(self):
        storage = SimpleNamespace(
            initialize=AsyncMock(),
            close=AsyncMock(),
        )
        web_server = SimpleNamespace(
            start=AsyncMock(),
            stop=AsyncMock(),
        )
        worker = SimpleNamespace(
            start=AsyncMock(side_effect=RuntimeError("worker failed")),
            stop=AsyncMock(),
        )
        app = SimpleNamespace(bot=object(), dp=object())

        with (
            patch(
                "writers_submission.runtime.PostgresWritersSubmissionStorage",
                return_value=storage,
            ),
            patch("writers_submission.runtime.WritersSubmissionService"),
            patch("writers_submission.runtime.WritersFileService"),
            patch("writers_submission.runtime.SessionSigner"),
            patch("writers_submission.runtime.create_writers_web_app", return_value=object()),
            patch(
                "writers_submission.runtime.WritersWebServer",
                return_value=web_server,
            ),
            patch(
                "writers_submission.runtime.WritersDeliveryWorker",
                return_value=worker,
            ),
            patch("writers_submission.runtime.register_writers_submission_handlers"),
        ):
            with self.assertRaisesRegex(RuntimeError, "worker failed"):
                await start_writers_submission_runtime(
                    app,
                    self.config(),
                    database_url="postgresql://user:pass@db/name",
                    bot_token="token",
                )

        worker.stop.assert_awaited_once()
        web_server.stop.assert_awaited_once()
        storage.close.assert_awaited_once()

    async def test_runtime_stop_is_idempotent(self):
        storage = SimpleNamespace(initialize=AsyncMock(), close=AsyncMock())
        web_server = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
        worker = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
        app = SimpleNamespace(bot=object(), dp=object())

        with (
            patch(
                "writers_submission.runtime.PostgresWritersSubmissionStorage",
                return_value=storage,
            ),
            patch("writers_submission.runtime.WritersSubmissionService"),
            patch("writers_submission.runtime.WritersFileService"),
            patch("writers_submission.runtime.SessionSigner"),
            patch("writers_submission.runtime.create_writers_web_app", return_value=object()),
            patch(
                "writers_submission.runtime.WritersWebServer",
                return_value=web_server,
            ),
            patch(
                "writers_submission.runtime.WritersDeliveryWorker",
                return_value=worker,
            ),
            patch("writers_submission.runtime.register_writers_submission_handlers"),
        ):
            runtime = await start_writers_submission_runtime(
                app,
                self.config(),
                database_url="postgresql://user:pass@db/name",
                bot_token="token",
            )

        await runtime.stop()
        await runtime.stop()
        worker.stop.assert_awaited_once()
        web_server.stop.assert_awaited_once()
        storage.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()

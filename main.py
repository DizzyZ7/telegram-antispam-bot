"""Application entry point with scoped writers-chat moderation."""

from __future__ import annotations

import asyncio
import os

os.environ.setdefault("WRITERS_CHAT_ID", "-1002619489118")

import legacy_main as app
from writers_moderation import register_writers_chat_handlers


async def main() -> None:
    scope = register_writers_chat_handlers(app)
    await scope.resolve(app.bot)

    if scope.chat_id is not None and scope.chat_id not in app.ALLOWED_CHATS:
        app.ALLOWED_CHATS.append(scope.chat_id)

    await app.main()


if __name__ == "__main__":
    asyncio.run(main())

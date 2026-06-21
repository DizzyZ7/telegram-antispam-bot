"""Stable bot startup."""

import asyncio

import legacy_main as app
from writers_moderation import register_writers_chat_handlers


async def main() -> None:
    scope = register_writers_chat_handlers(app)
    await scope.resolve(app.bot)
    await app.main()


if __name__ == "__main__":
    asyncio.run(main())

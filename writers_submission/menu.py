"""Bot-wide Telegram chat-menu entry for Writers Submission Mini App.

The menu is visible beside the message composer in private chats. It does not
grant access: existing server-side Telegram initData and writers membership
checks remain authoritative.
"""

from __future__ import annotations

from typing import Any

from aiogram.types import MenuButtonWebApp, WebAppInfo


async def install_writers_submission_menu(bot: Any, *, public_url: str) -> None:
    result = await bot.set_chat_menu_button(
        menu_button=MenuButtonWebApp(
            text="✒️ Отправить заявку",
            web_app=WebAppInfo(url=str(public_url)),
        )
    )
    if result is not True:
        raise RuntimeError("Telegram did not confirm Writers menu button")

"""Isolated chat scope for the Lexicon mini-game.

Chats listed here are visible only to the Lexicon service. They are intentionally
not added to legacy_main.ALLOWED_CHATS, so moderation, summaries, reactions,
captcha and other bot features remain disabled there.
"""

from __future__ import annotations

import os
from typing import Any

DEFAULT_LEXICON_ONLY_CHATS = frozenset({-1002659916114})


def parse_lexicon_only_chats() -> frozenset[int]:
    raw = os.getenv("LEXICON_ONLY_CHATS")
    if not raw:
        return DEFAULT_LEXICON_ONLY_CHATS

    chat_ids: set[int] = set()
    for item in raw.replace(";", ",").split(","):
        chunk = item.strip()
        if chunk:
            chat_ids.add(int(chunk))
    return frozenset(chat_ids)


class LexiconAppProxy:
    """Delegate the application API while widening only Lexicon chat access."""

    def __init__(self, app: Any, lexicon_only_chats: frozenset[int] | None = None) -> None:
        self._app = app
        self.lexicon_only_chats = lexicon_only_chats or parse_lexicon_only_chats()

    def is_allowed_chat(self, chat_id: int) -> bool:
        return bool(self._app.is_allowed_chat(chat_id) or chat_id in self.lexicon_only_chats)

    def is_lexicon_only_chat(self, chat_id: int) -> bool:
        return chat_id in self.lexicon_only_chats and not self._app.is_allowed_chat(chat_id)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._app, name)


def build_lexicon_app(app: Any) -> LexiconAppProxy:
    proxy = LexiconAppProxy(app)
    print(
        "LEXICON_CHAT_SCOPE_READY "
        f"lexicon_only={','.join(str(chat_id) for chat_id in sorted(proxy.lexicon_only_chats))} "
        "other_features=off topics=optional",
        flush=True,
    )
    return proxy


__all__ = ["LexiconAppProxy", "build_lexicon_app", "parse_lexicon_only_chats"]

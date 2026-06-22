"""Application entry point with scoped writers-chat moderation."""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

os.environ.setdefault("WRITERS_CHAT_ID", "-1002619489118")

APP_DIR = Path(__file__).resolve().parent
BUNDLED_LEXICON_PATH = APP_DIR / "bundled_moderation_lexicon.json"
RUNTIME_DATA_DIR = Path(os.getenv("DATA_DIR", APP_DIR / "data"))
RUNTIME_LEXICON_PATH = RUNTIME_DATA_DIR / "moderation_lexicon.json"


def sync_moderation_lexicon() -> None:
    if not BUNDLED_LEXICON_PATH.is_file():
        raise RuntimeError(f"Bundled moderation lexicon is missing: {BUNDLED_LEXICON_PATH}")

    RUNTIME_DATA_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(BUNDLED_LEXICON_PATH, RUNTIME_LEXICON_PATH)
    print(
        "WRITERS_LEXICON_SYNCED "
        f"source={BUNDLED_LEXICON_PATH.name} target={RUNTIME_LEXICON_PATH} "
        f"bytes={RUNTIME_LEXICON_PATH.stat().st_size}",
        flush=True,
    )


sync_moderation_lexicon()

import legacy_main as app
from writers_moderation import MODERATION_LEXICON, register_writers_chat_handlers


async def main() -> None:
    scope = register_writers_chat_handlers(app)
    await scope.resolve(app.bot)

    if scope.chat_id is not None and scope.chat_id not in app.ALLOWED_CHATS:
        app.ALLOWED_CHATS.append(scope.chat_id)

    print(
        "WRITERS_MODERATION_READY "
        f"chat_id={scope.chat_id} rules={MODERATION_LEXICON.rule_count}",
        flush=True,
    )

    await app.main()


if __name__ == "__main__":
    asyncio.run(main())

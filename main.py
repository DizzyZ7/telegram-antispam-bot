"""Application entry point with scoped writers-chat moderation."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

os.environ.setdefault("WRITERS_CHAT_ID", "-1002619489118")

LOGGER = logging.getLogger(__name__)
APP_DIR = Path(__file__).resolve().parent
BUNDLED_LEXICON_PATH = APP_DIR / "bundled_moderation_lexicon.json"
RUNTIME_DATA_DIR = Path(os.getenv("DATA_DIR", APP_DIR / "data"))
RUNTIME_LEXICON_PATH = RUNTIME_DATA_DIR / "moderation_lexicon.json"
IGNORED_WRITERS_TOPIC_IDS = frozenset({14637, 42817, 292358})
ASCII_LATIN_PATTERN = re.compile(r"[A-Za-z]")
CYRILLIC_PATTERN = re.compile(r"[А-Яа-яЁё]")

RUNTIME_RULE_OVERLAY: dict[str, dict[str, tuple[str, ...]]] = {
    "exact": {
        "obscene": ("\u0431\u0434\u044c",),
        "english_obscene": ("xj",),
    },
    "prefix": {
        "obscene": ("\u0445\u0439", "\u043f\u0437\u0434"),
        "latin_translit": ("pzd",),
    },
}

UNSAFE_MIXED_PREFIXES = frozenset({"\u043d\u0443", "\u043e\u0431\u043e\u0441", "\u043f\u0430\u0434\u043b"})


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


def apply_runtime_rule_overlay() -> int:
    payload = json.loads(RUNTIME_LEXICON_PATH.read_text(encoding="utf-8"))
    rules = payload.setdefault("rules", {})
    added = 0

    for match_mode, categories in RUNTIME_RULE_OVERLAY.items():
        target_categories = rules.setdefault(match_mode, {})
        for category, terms in categories.items():
            target_terms = target_categories.setdefault(category, [])
            known_terms = set(target_terms)
            for term in terms:
                if term not in known_terms:
                    target_terms.append(term)
                    known_terms.add(term)
                    added += 1

    RUNTIME_LEXICON_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"WRITERS_LEXICON_OVERLAY_READY added={added}", flush=True)
    return added


def _is_pure_latin_rule(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(ASCII_LATIN_PATTERN.search(value))
        and not CYRILLIC_PATTERN.search(value)
    )


def sanitize_compiled_lexicon() -> tuple[int, int]:
    """Remove unsafe Cyrillic copies of Latin rules and ambiguous short prefixes."""
    import writers_moderation as moderation

    payload = json.loads(RUNTIME_LEXICON_PATH.read_text(encoding="utf-8"))
    latin_exact_collisions: set[str] = set()
    latin_prefix_collisions: set[str] = set()

    for match_mode, categories in payload.get("rules", {}).items():
        if match_mode not in {"exact", "prefix"} or not isinstance(categories, dict):
            continue
        for terms in categories.values():
            if not isinstance(terms, list):
                continue
            for raw_term in terms:
                if not _is_pure_latin_rule(raw_term):
                    continue
                mixed_term = moderation._normalize_mixed_token(raw_term)
                if not mixed_term:
                    continue
                if match_mode == "exact":
                    latin_exact_collisions.add(mixed_term)
                else:
                    latin_prefix_collisions.add(mixed_term)

    exact_mixed = {
        term: category
        for term, category in moderation.MODERATION_LEXICON.exact_mixed.items()
        if term not in latin_exact_collisions
    }
    prefixes_without_latin_collisions = tuple(
        item
        for item in moderation.MODERATION_LEXICON.prefix_mixed
        if item[0] not in latin_prefix_collisions
    )
    prefix_mixed = tuple(
        item
        for item in prefixes_without_latin_collisions
        if item[0] not in UNSAFE_MIXED_PREFIXES
    )
    removed_latin = (
        len(moderation.MODERATION_LEXICON.exact_mixed) - len(exact_mixed)
        + len(moderation.MODERATION_LEXICON.prefix_mixed) - len(prefixes_without_latin_collisions)
    )
    removed_ambiguous = len(prefixes_without_latin_collisions) - len(prefix_mixed)

    moderation.MODERATION_LEXICON = replace(
        moderation.MODERATION_LEXICON,
        exact_mixed=exact_mixed,
        prefix_mixed=prefix_mixed,
        rule_count=max(0, moderation.MODERATION_LEXICON.rule_count - removed_latin - removed_ambiguous),
    )
    print(
        "WRITERS_LEXICON_SAFETY_PATCH "
        f"removed_latin_collisions={removed_latin} "
        f"removed_ambiguous_prefixes={removed_ambiguous}",
        flush=True,
    )
    return removed_latin, removed_ambiguous


def install_ignored_topic_filter() -> None:
    import writers_moderation as moderation

    original_call = moderation.ProhibitedLanguageFilter.__call__

    async def scoped_call(instance: object, message: object) -> bool:
        if getattr(message, "message_thread_id", None) in IGNORED_WRITERS_TOPIC_IDS:
            return False
        return await original_call(instance, message)

    moderation.ProhibitedLanguageFilter.__call__ = scoped_call
    print(
        "WRITERS_TOPIC_EXCLUSIONS_READY "
        f"ids={','.join(str(value) for value in sorted(IGNORED_WRITERS_TOPIC_IDS))}",
        flush=True,
    )


def _telegram_day_bounds(message_date: datetime, tz: Any) -> tuple[int, int, datetime]:
    if message_date.tzinfo is None:
        message_date = message_date.replace(tzinfo=timezone.utc)
    local_now = message_date.astimezone(tz)
    local_day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(local_day_start.timestamp()), int(local_now.timestamp()), local_now


async def build_stats_from_command_time(service: Any, chat_id: int, message_date: datetime) -> str:
    start_ts, end_ts, local_now = _telegram_day_bounds(message_date, service.tz)
    messages = await service.storage.get_messages_between(chat_id, start_ts, end_ts)
    participants = await service.storage.get_top_participants(chat_id, start_ts, end_ts, limit=5)
    sticker_count, reaction_count, top_reactions = await service.storage.get_engagement_events(
        chat_id,
        start_ts,
        end_ts,
    )
    emoji_count, emoji_messages, emoji_only_messages = service._build_emoji_stats(messages)
    text_total = len(messages)
    total_activity = text_total + sticker_count + reaction_count

    if total_activity == 0:
        return "📊 За сегодня в чате пока нет данных активности."

    lines = [
        f"📊 Краткая статистика за сегодня на {local_now:%H:%M}:",
        f"Текстовых сообщений: <b>{text_total}</b>",
        f"Стикеров: <b>{sticker_count}</b>",
        f"Эмодзи в текстах: <b>{emoji_count}</b>",
        f"Сообщений с эмодзи: <b>{emoji_messages}</b>",
        f"Сообщений только из эмодзи: <b>{emoji_only_messages}</b>",
        f"Реакций: <b>{reaction_count}</b>",
        "Самые активные участники:",
    ]

    if participants:
        for stat in participants:
            lines.append(f"— {service._display_name(stat)}: {stat.message_count}")
    else:
        lines.append("— Нет данных")

    if top_reactions:
        lines.append("Топ реакций:")
        for reaction, count in top_reactions[:3]:
            lines.append(f"— {app.reaction_label(reaction)}: {count}")

    if text_total < service.min_messages:
        lines.append("")
        lines.append("Сегодня в чате было мало сообщений для полноценной аналитики.")

    return "\n".join(lines)


def install_stats_command_handler(app: object) -> None:
    """Register /stats after moderation and move it to the front of message handlers."""
    command_filter = getattr(app, "Command")
    dispatcher = getattr(app, "dp")

    @dispatcher.message(command_filter("stats"))
    async def priority_stats_command(message: Any) -> None:
        LOGGER.info(
            "Stats command received chat_id=%s message_id=%s telegram_date=%s",
            message.chat.id,
            message.message_id,
            message.date.isoformat(),
        )
        if not app.is_group_chat(message) or not app.is_allowed_chat(message.chat.id):
            LOGGER.info("Stats command skipped for non-allowed chat_id=%s", message.chat.id)
            return

        service = app.summary_service
        if service is None:
            LOGGER.warning("Stats command received before summary service initialization")
            await message.reply("⌛ Статистика еще запускается. Попробуй еще раз через несколько секунд.")
            return

        try:
            text = await build_stats_from_command_time(service, message.chat.id, message.date)
        except Exception:
            LOGGER.exception("Could not build stats for chat_id=%s", message.chat.id)
            await message.reply("⚠️ Не удалось собрать статистику. Попробуй еще раз через минуту.")
            return

        try:
            await message.reply(text)
        except Exception:
            LOGGER.exception("Could not send stats reply for chat_id=%s", message.chat.id)
            return
        LOGGER.info("Stats command completed for chat_id=%s", message.chat.id)

    dispatcher.message.handlers.insert(0, dispatcher.message.handlers.pop())
    print("WRITERS_STATS_COMMAND_READY source=telegram_message_date", flush=True)


sync_moderation_lexicon()
apply_runtime_rule_overlay()

import writers_moderation

sanitize_compiled_lexicon()
install_ignored_topic_filter()

import legacy_main as app
from writers_moderation import MODERATION_LEXICON, register_writers_chat_handlers


async def main() -> None:
    scope = register_writers_chat_handlers(app)
    install_stats_command_handler(app)
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

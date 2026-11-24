"""Direct, persistent, one-time birthday delivery independent of Entertainment.

This is deliberately a one-day production recovery task. It runs during
main.py startup (and can be invoked manually) before the entertainment
supervisor starts. It uses a separate PostgreSQL receipt and directs the
message as a reply to Fosgen's known 11:11 congratulations, so Telegram
places it in the same discussion.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import asyncpg
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramNotFound,
    TelegramRetryAfter,
    TelegramUnauthorizedError,
)
from aiogram.types import ReplyParameters

CHAT_ID = -1002619489118
SOURCE_MESSAGE_ID = 586679
EVENT_ID = "ikf-2026-11-24-pupsan99-one-shot-direct"
MOMENT = datetime(2026, 11, 24, 13, 20, tzinfo=ZoneInfo("Europe/Moscow"))
DEADLINE = datetime(2026, 11, 25, 0, 0, tzinfo=ZoneInfo("Europe/Moscow"))
TEXT = "@pupsan99, с днем рождения! 🎂💛\n\nСегодня у меня особенный повод сказать тебе спасибо. Ведь именно благодаря тебе когда-то появился я — Фосген! Получается, сегодня я поздравляю человека, без которого не было бы и меня. И, между прочим, это очень серьезное дело для маленького цифрового жителя ИКФ. 🐾💛\n\nТы создала не просто канал, а настоящий дом для историй и людей, которые их любят. Здесь писатели находят вдохновение, читатели — новые миры, а самые разные люди — своих. За таким местом стоит огромное количество сил, заботы и сердца. И я очень хочу, чтобы сегодня все это добро вернулось к тебе сторицей. 📖✨\n\nЖелаю тебе здоровья, любви, душевного спокойствия и времени на себя. Пусть вдохновение приходит вовремя, персонажи хотя бы иногда слушаются автора, а каждая новая глава — и написанная, и прожитая — приносит радость. Пусть рядом будут люди, с которыми можно быть собой, смеяться до слез и чувствовать себя по-настоящему счастливой. 🌷\n\nА теперь чрезвычайное происшествие: я отправил енота за праздничным тортом. Он вернулся без торта, зато объявил себя главным редактором ИКФ и потребовал гонорар печеньем. 🦝🍪 Еноту пора! Пора вернуть торт, перестать редактировать чужие главы и дать нашей имениннице спокойно праздновать! 😂🎂\n\nСпасибо тебе за ИКФ. И отдельно — за то, что благодаря тебе появился Фосген. Я, конечно, всего лишь бот, но обнимать виртуальными лапками умею от всей души.\n\nС днем рождения! Пусть впереди будет много света, теплых людей и прекрасных историй.\nТвой Фосген. 🐾💖"

_CONFIRMED_REJECTIONS = (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNotFound,
    TelegramRetryAfter,
    TelegramUnauthorizedError,
)


class PostgresOneShotJournal:
    """Independent DB journal. A unique event key guards every restart/worker."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    async def _connect(self):
        return await asyncpg.connect(dsn=self.database_url, timeout=10)

    async def claim(self, now: int) -> tuple[bool, str | None, int | None]:
        connection = await self._connect()
        try:
            await connection.execute(
                """CREATE TABLE IF NOT EXISTS fosgen_one_shot_delivery (
                    event_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    updated_at BIGINT NOT NULL,
                    telegram_message_id BIGINT
                )"""
            )
            # Explicit Bot API rejection means the message was not delivered:
            # retry is safe after permissions/config are corrected. Unknown
            # network outcomes and successful sends are NEVER auto-retried.
            claimed = await connection.fetchval(
                """INSERT INTO fosgen_one_shot_delivery
                   (event_id,status,updated_at,telegram_message_id)
                   VALUES ($1,'CLAIMED',$2,NULL)
                   ON CONFLICT(event_id) DO UPDATE
                     SET status='CLAIMED',updated_at=EXCLUDED.updated_at,
                         telegram_message_id=NULL
                     WHERE fosgen_one_shot_delivery.status='REJECTED'
                   RETURNING event_id""",
                EVENT_ID, int(now),
            )
            if claimed is not None:
                return True, "CLAIMED", None
            row = await connection.fetchrow(
                "SELECT status,telegram_message_id FROM fosgen_one_shot_delivery "
                "WHERE event_id=$1", EVENT_ID,
            )
            return False, str(row["status"]) if row else None, (
                int(row["telegram_message_id"])
                if row and row["telegram_message_id"] is not None else None
            )
        finally:
            await connection.close()

    async def finish(self, status: str, now: int, message_id: int | None) -> None:
        if status not in {"SENT", "UNCERTAIN", "REJECTED"}:
            raise ValueError("Invalid one-shot status")
        connection = await self._connect()
        try:
            await connection.execute(
                "UPDATE fosgen_one_shot_delivery "
                "SET status=$1,updated_at=$2,telegram_message_id=$3 "
                "WHERE event_id=$4 AND status='CLAIMED'",
                status, int(now), message_id, EVENT_ID,
            )
        finally:
            await connection.close()


def _log(message: str) -> None:
    print("PUPSAN_ONESHOT_" + message, flush=True)


async def send_pupsan_once(
    bot,
    database_url: str | None,
    *,
    when: datetime | None = None,
    journal=None,
) -> str:
    """Send exactly one *attempt* per durable claim; log the terminal outcome.

    Telegram does not support atomic DB+send transactions. If the network
    response is lost, mark UNCERTAIN rather than potentially duplicating.
    """
    current = (when or datetime.now(ZoneInfo("Europe/Moscow"))).astimezone(
        ZoneInfo("Europe/Moscow")
    )
    _log(f"HOOK_REACHED event={EVENT_ID} local_time={current.isoformat()}")
    if not MOMENT <= current < DEADLINE:
        _log("SKIPPED_OUTSIDE_WINDOW")
        return "outside_window"
    if journal is None:
        if not database_url:
            _log("ERROR database_url_missing; cannot deduplicate across rebuilds")
            return "no_database"
        journal = PostgresOneShotJournal(database_url)
    try:
        claimed, existing_status, existing_message_id = await journal.claim(int(current.timestamp()))
    except Exception as exc:
        _log(f"ERROR journal_claim_failed type={type(exc).__name__}")
        return "storage_error"
    if not claimed:
        _log(
            f"ALREADY_CLAIMED status={existing_status} message_id={existing_message_id} "
            f"link=https://t.me/chat_IKF/{existing_message_id or ''}"
        )
        return "already_claimed"

    _log(f"CLAIMED event={EVENT_ID} target_chat={CHAT_ID} reply_to={SOURCE_MESSAGE_ID}")
    try:
        try:
            sent = await bot.send_message(
                chat_id=CHAT_ID,
                text=TEXT,
                parse_mode=None,
                reply_parameters=ReplyParameters(
                    message_id=SOURCE_MESSAGE_ID,
                    allow_sending_without_reply=False,
                ),
            )
        except TelegramBadRequest as exc:
            # Explicit invalid-reply rejection means Telegram did not
            # accept that first attempt. Try the chat itself once.
            _log(f"REPLY_REJECTED reason={type(exc).__name__} fallback=chat")
            sent = await bot.send_message(
                chat_id=CHAT_ID,
                text=TEXT,
                parse_mode=None,
            )
    except _CONFIRMED_REJECTIONS as exc:
        try:
            await journal.finish("REJECTED", int(current.timestamp()), None)
        except Exception as journal_exc:
            _log(f"ERROR rejection_ack_failed type={type(journal_exc).__name__}")
        _log(f"REJECTED type={type(exc).__name__} detail={str(exc)[:200]}")
        return "rejected"
    except Exception as exc:
        try:
            await journal.finish("UNCERTAIN", int(current.timestamp()), None)
        except Exception as journal_exc:
            _log(f"ERROR unknown_ack_failed type={type(journal_exc).__name__}")
        _log(f"UNCERTAIN type={type(exc).__name__}; automatic_retry=disabled")
        return "uncertain"

    message_id = getattr(sent, "message_id", None)
    thread_id = getattr(sent, "message_thread_id", None)
    try:
        await journal.finish("SENT", int(current.timestamp()), message_id)
    except Exception as exc:
        _log(
            f"SENT_BUT_ACK_FAILED message_id={message_id} "
            f"type={type(exc).__name__} link=https://t.me/chat_IKF/{message_id}"
        )
        return "sent_ack_failed"

    _log(
        f"SENT message_id={message_id} thread_id={thread_id} "
        f"link=https://t.me/chat_IKF/{message_id}"
    )
    return "sent"


async def _cli() -> None:
    from aiogram import Bot
    from runtime_env import load_runtime_env
    load_runtime_env(Path(__file__).resolve().parent)
    token = (os.getenv("BOT_TOKEN") or "").strip()
    if not token:
        _log("ERROR bot_token_missing")
        raise SystemExit(2)
    async with Bot(token=token) as bot:
        result = await send_pupsan_once(bot, os.getenv("DATABASE_URL"))
    _log(f"CLI_FINISHED result={result}")


if __name__ == "__main__":
    asyncio.run(_cli())

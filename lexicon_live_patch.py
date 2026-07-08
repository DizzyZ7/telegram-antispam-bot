"""Live UX fixes for the Lexicon mini-game.

This module subclasses the existing mini-game service without rewriting the whole
large minigames.py file. It fixes the live round behavior that confused players:
accepted short words were counted silently, duplicate words were silent, and the
cooldown could react to ordinary chat messages before the message was known to be
a valid word attempt.
"""

from __future__ import annotations

import asyncio
import logging
import time

from aiogram.types import Message

from minigames import (
    ATMOSPHERIC_WORDS,
    HINT_LIMIT,
    MIN_WORD_LENGTH,
    ROUND_SECONDS,
    WORD_RE,
    MiniGameService,
    PlayerResult,
    WordGameRound,
)

LOGGER = logging.getLogger(__name__)

LIVE_EXTRA_WORDS = frozenset(
    {
        # common words players naturally try in rounds like "конструкторская"
        "контур",
        "трактор",
        "турок",
        "носок",
        "урок",
        "крот",
        "утка",
        "корт",
        "трос",
        "торс",
        "стук",
        "сукно",
        "ткань",
        "танк",
        "коса",
        "кора",
        "коса",
        "сорт",
        "сотка",
        "сотня",
        "скорняк",
        "скорняк",
        "настрой",
        "настой",
        "струна",
        "строка",
        "страна",
        "страус",
        "корона",
        "корка",
        "норка",
        "нора",
        "носка",
        "скот",
        "скат",
        "срок",
        "рост",
        "трон",
        "кран",
        "крот",
        "крон",
        "кросс",
        "коста",
        "актер",
        "терка",
        "сектор",
        "секатор",
    }
)


class PatchedMiniGameService(MiniGameService):
    """Lexicon service with visible word acknowledgements and pinned source page."""

    @classmethod
    def load_dictionary_words(cls) -> set[str]:
        words = set(super().load_dictionary_words())
        words.update(LIVE_EXTRA_WORDS)
        return words

    def found_words_line(self, round_data: WordGameRound, limit: int = 18) -> str | None:
        if not round_data.found_words:
            return None
        visible_words = round_data.found_words[-limit:]
        prefix = "Последние найденные" if len(round_data.found_words) > limit else "Уже найдено"
        words = " · ".join(f"<code>{self.app.safe_output_text(word)}</code>" for word in visible_words)
        hidden_count = max(0, len(round_data.found_words) - limit)
        tail = f"\nи еще <b>{hidden_count}</b> слов раньше" if hidden_count else ""
        return f"<b>{prefix}</b>\n{words}{tail}"

    def render_status(self, round_data: WordGameRound) -> str:
        left = int(round_data.ends_at - time.monotonic())
        found, total, percent, bar = self.round_stats(round_data)
        players = self.top_players(round_data, limit=7)
        hint_votes_required = self.required_hint_votes(round_data)
        lines = [
            "📖 <b>ЛЕКСИКОН · текущая страница</b>",
            "",
            f"<code>Раунд #{round_data.round_code}</code>",
            f"Осталось: <b>{self.format_duration(left)}</b>",
            "",
            f"<code>{bar}</code> <b>{percent}%</b>",
            f"Найдено слов: <b>{found}</b> из <b>{total}</b>",
            f"Намеков использовано: <b>{round_data.hint_count}</b>/<b>{HINT_LIMIT}</b>",
            f"Голосов за следующий намек: <b>{len(round_data.hint_requesters)}</b>/<b>{hint_votes_required}</b>",
            "",
            "<b>Сейчас в тексте</b>",
            "",
        ]
        if players:
            for idx, player in enumerate(players, start=1):
                lines.append(self.compact_player_line(idx, player))
        else:
            lines.append("▫️ Страница еще чистая. Первое слово ждет автора.")

        found_line = self.found_words_line(round_data)
        if found_line:
            lines.extend(("", found_line))

        lines.append("")
        lines.append("⌁ /hint · /stopgame · /game_top")
        return "\n".join(lines)

    async def pin_start_message(self, message: Message) -> None:
        try:
            await self.app.bot.pin_chat_message(
                chat_id=message.chat.id,
                message_id=message.message_id,
                disable_notification=True,
            )
        except Exception:
            LOGGER.info(
                "Could not pin Lexicon round message chat_id=%s message_id=%s",
                message.chat.id,
                message.message_id,
                exc_info=True,
            )

    async def start_word_game(self, message: Message) -> None:
        if not self.app.is_group_chat(message) or not self.app.is_allowed_chat(message.chat.id):
            return
        if message.from_user is None or message.from_user.is_bot:
            return

        game_key = self.round_key_from_message(message)
        async with self.lock:
            active = self.active_word_games.get(game_key)
            if active and active.ends_at > time.monotonic():
                await message.reply(self.render_status(active))
                return

            base_word, allowed = self.choose_base_word()
            now = time.monotonic()
            round_data = WordGameRound(
                chat_id=message.chat.id,
                round_code=self.round_code(),
                base_word=base_word,
                allowed_words=allowed,
                min_length=MIN_WORD_LENGTH,
                started_at=now,
                ends_at=now + ROUND_SECONDS,
                message_thread_id=message.message_thread_id,
            )
            self.active_word_games[game_key] = round_data
            round_data.finish_task = asyncio.create_task(self.finish_later(round_data))

        sent_message = await message.answer(self.render_start(round_data))
        await self.pin_start_message(sent_message)

    def compact_found_reply(
        self,
        *,
        player: PlayerResult,
        word: str,
        points: int,
        total_points: int,
        found: int,
        total: int,
        percent: int,
    ) -> str:
        return (
            "✒️ <b>Слово принято</b>\n\n"
            f"{self.app.safe_output_text(player.name)} записывает:\n"
            f"<code>{self.app.safe_output_text(word)}</code>\n\n"
            f"+{points}🌟 · всего у автора: <b>{total_points}🌟</b>\n"
            f"Страница заполнена на <b>{percent}%</b> — {found}/{total}"
        )

    def strong_found_reply(
        self,
        *,
        player: PlayerResult,
        word: str,
        points: int,
        total_points: int,
        found: int,
        total: int,
        percent: int,
    ) -> str:
        if word in ATMOSPHERIC_WORDS:
            return (
                "🕯 <b>Атмосферная находка</b>\n\n"
                f"<code>{self.app.safe_output_text(word)}</code>\n\n"
                f"+{points}🌟 · слово с послевкусием.\n"
                f"Всего у автора: <b>{total_points}🌟</b>\n"
                f"Страница заполнена на <b>{percent}%</b> — {found}/{total}"
            )

        if points >= 7:
            label = "💎 <b>Редкая находка</b>" if points >= 10 else "✨ <b>Сильная находка</b>"
            return (
                f"{label}\n\n"
                f"{self.app.safe_output_text(player.name)} забирает слово:\n"
                f"<code>{self.app.safe_output_text(word)}</code>\n\n"
                f"+{points}🌟\n"
                f"Всего у автора: <b>{total_points}🌟</b>\n"
                f"Страница заполнена на <b>{percent}%</b> — {found}/{total}"
            )

        return self.compact_found_reply(
            player=player,
            word=word,
            points=points,
            total_points=total_points,
            found=found,
            total=total,
            percent=percent,
        )

    async def handle_word_guess(self, message: Message) -> None:
        if not self.app.is_group_chat(message) or not self.app.is_allowed_chat(message.chat.id):
            return
        if message.from_user is None or message.from_user.is_bot:
            return
        if not message.text or message.text.startswith("/"):
            return

        round_data = self.active_word_games.get(self.round_key_from_message(message))
        if round_data is None or round_data.ends_at <= time.monotonic():
            return

        raw_word = message.text.strip()
        if " " in raw_word or "\n" in raw_word:
            return
        if not WORD_RE.match(raw_word):
            return

        word = self.normalize_word(raw_word)
        if len(word) < round_data.min_length:
            return
        if word == round_data.base_word:
            return
        if not self.can_build(word, round_data.base_word):
            return
        if word not in round_data.allowed_words:
            return

        async with round_data.lock:
            if word in round_data.used_words:
                owner_name = self.player_name_by_word(round_data, word)
                duplicate_reply = (
                    "📖 <b>Это слово уже есть на странице</b>\n\n"
                    f"<code>{self.app.safe_output_text(word)}</code>\n"
                    f"Его раньше забрал: <b>{self.app.safe_output_text(owner_name)}</b>"
                )
                accepted_reply = None
            else:
                points = self.word_points(word)
                player = round_data.players.get(message.from_user.id)
                if player is None:
                    player = PlayerResult(user_id=message.from_user.id, name=self.player_name(message))
                    round_data.players[message.from_user.id] = player
                player.points += points
                player.words[word] = points
                round_data.used_words[word] = message.from_user.id
                round_data.found_words.append(word)
                total_points = player.points
                found, total, percent, _ = self.round_stats(round_data)
                accepted_reply = self.strong_found_reply(
                    player=player,
                    word=word,
                    points=points,
                    total_points=total_points,
                    found=found,
                    total=total,
                    percent=percent,
                )
                duplicate_reply = None

        if duplicate_reply is not None:
            await message.reply(duplicate_reply)
        elif accepted_reply is not None:
            await message.reply(accepted_reply)

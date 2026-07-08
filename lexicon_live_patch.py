"""Live UX and dictionary engine for the Lexicon mini-game.

The Lexicon accepts correctly written Russian words in base form. It uses:
- the curated in-repository dictionary;
- additional live words from chat testing;
- pymorphy3 / OpenCorpora morphology for strict noun-lemma validation;
- wordfreq's Russian frequency list for a much larger answer and source-word pool.

Important game rule: inflected forms are rejected. For example, "станция" is valid,
but "станцией", "станцию", and "станции" are not valid for this game.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from functools import lru_cache

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
from wordgame_dictionary import BASE_WORDS

LOGGER = logging.getLogger(__name__)

try:
    import pymorphy3
except Exception:  # pragma: no cover - optional at import time until requirements are installed
    pymorphy3 = None

try:
    from wordfreq import top_n_list
except Exception:  # pragma: no cover - optional at import time until requirements are installed
    top_n_list = None

MORPH = pymorphy3.MorphAnalyzer() if pymorphy3 is not None else None
MIN_MORPH_SCORE = 0.45
ACCEPTED_POS = frozenset({"NOUN"})
WORD_FREQ_DICTIONARY_LIMIT = 45_000
WORD_FREQ_SOURCE_LIMIT = 7_500
SOURCE_CANDIDATE_LIMIT = 1_500
MIN_SOURCE_LENGTH = 10
MAX_SOURCE_LENGTH = 18
MIN_DYNAMIC_SOLUTIONS_PER_ROUND = 38
MAX_PLAYABLE_ROUNDS = 900

LIVE_EXTRA_WORDS = frozenset(
    {
        # common words players naturally try in rounds like "конструкторская"
        "контур", "трактор", "турок", "носок", "урок", "крот", "утка", "корт",
        "трос", "торс", "стук", "сукно", "ткань", "танк", "коса", "кора", "сорт",
        "сотка", "сотня", "скорняк", "настрой", "настой", "струна", "строка",
        "страна", "страус", "корона", "корка", "норка", "нора", "носка", "скот",
        "скат", "срок", "рост", "трон", "кран", "крон", "кросс", "коста",
        "актер", "терка", "сектор", "секатор",
    }
)


def _letters_signature(word: str) -> tuple[int, ...]:
    counts = Counter(word)
    return tuple(counts.get(chr(code), 0) for code in range(ord("а"), ord("я") + 1))


def _signature_fits(word_signature: tuple[int, ...], source_signature: tuple[int, ...]) -> bool:
    return all(needed <= available for needed, available in zip(word_signature, source_signature, strict=False))


class PatchedMiniGameService(MiniGameService):
    """Lexicon service with large Russian dictionary and visible acknowledgements."""

    def __init__(self, app, storage) -> None:
        super().__init__(app, storage)
        print(
            "LEXICON_SOURCE_POOL_READY "
            f"answers={len(self.dictionary_words)} playable_sources={len(self.round_candidates)} "
            f"morphology={'on' if MORPH is not None else 'off'} "
            f"wordfreq={'on' if top_n_list is not None else 'off'}",
            flush=True,
        )

    @staticmethod
    @lru_cache(maxsize=150_000)
    def is_valid_dictionary_lemma(word: str) -> bool:
        """Return True for valid Russian noun lemmas, not arbitrary inflected forms.

        Examples:
        - станция -> True
        - станцией -> False, because normal form is станция
        - станции -> False, because it is an inflected/plural form in this game
        """
        if MORPH is None:
            return False
        parses = MORPH.parse(word)
        for parse in parses[:5]:
            tag = parse.tag
            normal_form = parse.normal_form.replace("ё", "е")
            if parse.score < MIN_MORPH_SCORE:
                continue
            if tag.POS not in ACCEPTED_POS:
                continue
            if normal_form != word:
                continue
            # Nominal lemma. Plural-only nouns such as "сани" stay valid because their normal_form is themselves.
            if "nomn" in tag or normal_form == word:
                return True
        return False

    @classmethod
    def _wordfreq_words(cls, limit: int) -> list[str]:
        if top_n_list is None:
            return []
        try:
            return [cls.normalize_word(word) for word in top_n_list("ru", limit)]
        except Exception:
            LOGGER.exception("Could not load Russian wordfreq list")
            return []

    @classmethod
    def load_dictionary_words(cls) -> set[str]:
        words = {cls.normalize_word(word) for word in super().load_dictionary_words()}
        words.update(LIVE_EXTRA_WORDS)
        words.update(cls._wordfreq_words(WORD_FREQ_DICTIONARY_LIMIT))
        filtered: set[str] = set()
        for word in words:
            if len(word) < MIN_WORD_LENGTH or not WORD_RE.match(word):
                continue
            if MORPH is not None and not cls.is_valid_dictionary_lemma(word):
                continue
            filtered.add(word)
        return filtered

    @classmethod
    def load_source_words(cls) -> list[str]:
        source_words: list[str] = []
        seen: set[str] = set()
        raw_candidates = [*BASE_WORDS, *cls._wordfreq_words(WORD_FREQ_SOURCE_LIMIT)]
        for raw_word in raw_candidates:
            word = cls.normalize_word(raw_word)
            if word in seen:
                continue
            if not (MIN_SOURCE_LENGTH <= len(word) <= MAX_SOURCE_LENGTH):
                continue
            if not WORD_RE.match(word):
                continue
            if MORPH is not None and word not in {cls.normalize_word(item) for item in BASE_WORDS}:
                if not cls.is_valid_dictionary_lemma(word):
                    continue
            seen.add(word)
            source_words.append(word)
            if len(source_words) >= SOURCE_CANDIDATE_LIMIT:
                break
        return source_words

    def build_round_candidates(self) -> list[tuple[str, set[str]]]:
        answer_words = sorted(self.dictionary_words, key=lambda item: (len(item), item))
        answer_signatures = {word: _letters_signature(word) for word in answer_words}
        candidates: list[tuple[str, set[str]]] = []
        best_fallback: tuple[str, set[str]] | None = None

        for base_word in self.load_source_words():
            base_signature = _letters_signature(base_word)
            allowed = {
                word
                for word in answer_words
                if word != base_word
                and len(word) <= len(base_word)
                and _signature_fits(answer_signatures[word], base_signature)
            }
            if best_fallback is None or len(allowed) > len(best_fallback[1]):
                best_fallback = (base_word, allowed)
            if len(allowed) >= MIN_DYNAMIC_SOLUTIONS_PER_ROUND:
                candidates.append((base_word, allowed))

        if not candidates:
            return [best_fallback] if best_fallback is not None else []

        # Keep a broad but not huge playable set for quick random choice.
        candidates.sort(key=lambda item: (len(item[1]), len(item[0])), reverse=True)
        return candidates[:MAX_PLAYABLE_ROUNDS]

    def choose_base_word(self) -> tuple[str, set[str]]:
        if not self.round_candidates:
            return super().choose_base_word()

        # 20% legendary/rich pages, 80% varied pages.
        if len(self.round_candidates) > 30 and self.app.random.random() < 0.2 if hasattr(self.app, "random") else False:
            pool = self.round_candidates[:30]
        else:
            pool = self.round_candidates[: min(len(self.round_candidates), 350)]
        import random

        base_word, allowed = random.choice(pool)
        return base_word, set(allowed)

    @staticmethod
    def page_level(total_words: int) -> str:
        if total_words >= 150:
            return "легендарная страница"
        if total_words >= 100:
            return "богатая страница"
        if total_words >= 65:
            return "широкая страница"
        return "камерная страница"

    def word_is_accepted_for_round(self, word: str, round_data: WordGameRound) -> bool:
        if word in round_data.allowed_words:
            return True
        if not self.is_valid_dictionary_lemma(word):
            return False
        if not self.can_build(word, round_data.base_word):
            return False
        # Add dynamic valid lemmas to the current round, so /game and final stats stay honest.
        round_data.allowed_words.add(word)
        return True

    def render_start(self, round_data: WordGameRound) -> str:
        total = len(round_data.allowed_words)
        return (
            f"<code>{self.spaced_word(round_data.base_word)}</code>\n"
            "📖 <b>ЛЕКСИКОН открыт</b>\n\n"
            f"<code>Раунд #{round_data.round_code}</code> · <b>{self.page_level(total)}</b>\n"
            "Слово-источник выше — именно его держим в закрепе.\n\n"
            f"В стартовом словаре страницы уже есть <b>{total}</b> слов.\n"
            "Лексикон также проверяет большой русский словарь начальных форм.\n"
            f"Минимум — <b>{round_data.min_length}</b> буквы.\n"
            f"Время до закрытия страницы — <b>{self.format_duration(ROUND_SECONDS)}</b>.\n\n"
            "Пишите слова прямо в чат.\n"
            "Первый, кто нашел слово, забирает его себе.\n"
            "Принимаются существительные в начальной форме: например, <b>станция</b>, но не <b>станцией</b>.\n\n"
            "Чем длиннее слово, тем больше звезд:\n"
            "4 буквы — 1🌟\n"
            "5 букв — 2🌟\n"
            "6 букв — 4🌟\n"
            "7 букв — 7🌟\n"
            "8+ букв — 10🌟 и выше\n\n"
            "Намеки открываются не сразу и не по одному голосу в активном раунде.\n\n"
            "⌁ Страница раунда: /game\n"
            "⌁ Намек: /hint\n"
            "⌁ Закрыть досрочно: /stopgame"
        )

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
            f"<code>{self.spaced_word(round_data.base_word)}</code>",
            "📖 <b>ЛЕКСИКОН · текущая страница</b>",
            "",
            f"<code>Раунд #{round_data.round_code}</code> · <b>{self.page_level(total)}</b>",
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
            try:
                await message.reply(
                    "📌 Не смог закрепить страницу Лексикона.\n"
                    "Проверь, что Fosgen — администратор и у него есть право закреплять сообщения."
                )
            except Exception:
                LOGGER.info("Could not send Lexicon pin failure notice", exc_info=True)

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
        if not self.word_is_accepted_for_round(word, round_data):
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

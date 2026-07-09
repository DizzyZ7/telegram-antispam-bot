"""Community learning layer for the Lexicon mini-game.

Why this exists:
- the built-in morphology/frequency dictionary gives broad coverage;
- optional Hunspell spelling dictionary catches many ordinary Russian words;
- real players will still find valid words that a library misses;
- admins need a fast way to add approved words without editing code.

Persistent files live in DATA_DIR:
- lexicon_approved_words.txt  — one approved base-form noun per line;
- lexicon_rejected_words.log  — observed rejected candidates for review.
"""

from __future__ import annotations

import logging
import os
import re
import time
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any

from aiogram import F
from aiogram.filters import Command
from aiogram.types import Message

from lexicon_dictionary_patch import DictionaryBackedLexiconService
from minigames import WORD_RE

LOGGER = logging.getLogger(__name__)
DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
APPROVED_WORDS_PATH = DATA_DIR / "lexicon_approved_words.txt"
REJECTED_WORDS_LOG_PATH = DATA_DIR / "lexicon_rejected_words.log"
MAX_PENDING_LINES = 25
LEXICON_RAW_WORD_RE = re.compile(r"^[а-яА-ЯеЕёЁ-]{4,32}$")
LEXICON_STORED_WORD_RE = re.compile(r"^[а-яе]{4,32}$")
LEXICON_TEXT_COMMAND_RE = re.compile(
    r"^/(lex_add|lexicon_add|lex_pending|lexicon_pending|lex_check|lexicon_check)(?:@[A-Za-z0-9_]{1,32})?(?:\s+(.+))?$",
    re.IGNORECASE,
)
INVALID_WORD_MESSAGE = "Слово должно быть русским словом длиной от 4 до 32 символов."
UNCERTAIN_NOUN_MESSAGE = "Слово не похоже на существительное в начальной форме."
CURATED_COMMON_WORDS = frozenset(
    {
        "репа", "дрель", "трель", "след", "слет", "деталь", "педаль", "лепта", "перс",
        "сель", "лесть", "среда", "даль", "предатель", "седло", "адрес", "степь",
        "предел", "плеть", "тред", "десерт", "редька", "десна", "леска", "дело",
        "тело", "лето", "село", "стол", "рост", "трос", "сорт", "лист", "лиса",
        "сила", "соль", "роль", "ноль", "поле", "море", "гора", "кора", "коса",
        "нора", "нота", "тема", "мера", "рама", "рана", "рука", "река", "вера",
        "нерв", "сено", "сени", "овес", "овин", "овен", "вино", "веко", "окно",
        "окоп", "песок", "носок", "веник", "венок", "пион", "пирс", "риск",
        "срок", "сквер", "спор", "спрос", "сноп", "снос", "крен", "кров",
        "крон", "кран", "контур", "трактор", "турок", "сектор", "секатор",
    }
)


def _normalize_word(value: str) -> str:
    return value.strip().lower().replace("ё", "е")


def _stored_word(value: str) -> str:
    return _normalize_word(value).replace("-", "")


@lru_cache(maxsize=150_000)
def _hunspell_knows_safe(word: str) -> bool:
    """Lazy optional Hunspell check.

    Hunspell is a bonus layer only. Any import/download/parser problem must not
    prevent the bot from starting or serving the chat.
    """
    try:
        from lexicon_hunspell import hunspell_knows
    except BaseException as exc:  # noqa: BLE001 - keep bot alive even on bad optional dependency
        LOGGER.warning("Hunspell disabled after lazy import failure: %s", exc, exc_info=True)
        return False

    try:
        return bool(hunspell_knows(word))
    except BaseException as exc:  # noqa: BLE001 - optional dictionary must never crash gameplay
        LOGGER.warning("Hunspell lookup disabled for word=%s: %s", word, exc, exc_info=True)
        return False


def _known_dictionary_word(service: DictionaryBackedLexiconService, word: str) -> bool:
    return (
        service.is_valid_dictionary_lemma(word)
        or _hunspell_knows_safe(word)
        or word in CURATED_COMMON_WORDS
    )


def validate_lexicon_word(service: DictionaryBackedLexiconService, raw_word: str, *, admin_override: bool = False) -> tuple[bool, str]:
    """Validate a Lexicon word.

    The admin override bypasses only uncertain morphology/dictionary knowledge.
    It never bypasses length or character checks, so words like "кот" or
    "abc123" remain invalid.
    """
    raw_normalized = _normalize_word(raw_word)
    if not LEXICON_RAW_WORD_RE.fullmatch(raw_normalized):
        return False, INVALID_WORD_MESSAGE

    stored_word = _stored_word(raw_word)
    if not LEXICON_STORED_WORD_RE.fullmatch(stored_word):
        return False, INVALID_WORD_MESSAGE

    if _known_dictionary_word(service, stored_word):
        return True, stored_word
    if admin_override:
        return True, stored_word
    return False, UNCERTAIN_NOUN_MESSAGE


class LearningLexiconService(DictionaryBackedLexiconService):
    """Lexicon with admin-approved words and rejected-word review log."""

    approved_words: set[str] = set()

    @classmethod
    def load_approved_words(cls) -> set[str]:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if not APPROVED_WORDS_PATH.is_file():
            return set()
        words: set[str] = set()
        try:
            for line in APPROVED_WORDS_PATH.read_text(encoding="utf-8").splitlines():
                ok, result = validate_lexicon_word(cls, line, admin_override=True)  # type: ignore[arg-type]
                if ok:
                    words.add(result)
        except Exception:
            LOGGER.exception("Could not load approved Lexicon words from %s", APPROVED_WORDS_PATH)
        return words

    @classmethod
    def save_approved_words(cls) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        payload = "\n".join(sorted(cls.approved_words)) + ("\n" if cls.approved_words else "")
        APPROVED_WORDS_PATH.write_text(payload, encoding="utf-8")

    @classmethod
    def load_dictionary_words(cls) -> set[str]:
        cls.approved_words = cls.load_approved_words()
        words = super().load_dictionary_words()
        words.update(CURATED_COMMON_WORDS)
        words.update(cls.approved_words)
        LOGGER.info(
            "Learning Lexicon dictionary loaded: total=%s curated=%s approved=%s hunspell=lazy_optional",
            len(words),
            len(CURATED_COMMON_WORDS),
            len(cls.approved_words),
        )
        return words

    def add_approved_word(self, raw_word: str, *, admin_override: bool = True) -> tuple[bool, str]:
        ok, result = validate_lexicon_word(self, raw_word, admin_override=admin_override)
        if not ok:
            return False, result
        word = result
        self.approved_words.add(word)
        self.dictionary_words.add(word)
        self.save_approved_words()

        for _key, round_data in self.active_word_games.items():
            if self.can_build(word, round_data.base_word):
                round_data.allowed_words.add(word)
        return True, word

    def log_rejected_candidate(self, *, word: str, round_base: str, user_id: int, name: str, reason: str) -> None:
        if not LEXICON_STORED_WORD_RE.fullmatch(word):
            return
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        safe_name = name.replace("\n", " ").replace("\t", " ")[:80]
        row = f"{int(time.time())}\t{word}\t{round_base}\t{user_id}\t{safe_name}\t{reason}\n"
        try:
            with REJECTED_WORDS_LOG_PATH.open("a", encoding="utf-8") as file:
                file.write(row)
        except Exception:
            LOGGER.exception("Could not log rejected Lexicon word")

    def rejection_reason(self, word: str, round_data: Any) -> str | None:
        if len(word) < round_data.min_length:
            return "too_short"
        if word == round_data.base_word:
            return "source_word"
        if not self.can_build(word, round_data.base_word):
            return "letters_do_not_fit"
        if word in self.approved_words or _known_dictionary_word(self, word):
            return None
        ok, _ = validate_lexicon_word(self, word, admin_override=False)
        if not ok:
            return "not_base_noun"
        return None

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

        word = _stored_word(raw_word)
        reason = self.rejection_reason(word, round_data)
        if reason is not None:
            self.log_rejected_candidate(
                word=word,
                round_base=round_data.base_word,
                user_id=message.from_user.id,
                name=self.player_name(message),
                reason=reason,
            )
            return

        if (word in self.approved_words or _known_dictionary_word(self, word)) and word not in round_data.allowed_words:
            if not self.can_build(word, round_data.base_word):
                return
            round_data.allowed_words.add(word)

        await super().handle_word_guess(message)

    def pending_words(self, limit: int = MAX_PENDING_LINES) -> list[tuple[str, int, str]]:
        if not REJECTED_WORDS_LOG_PATH.is_file():
            return []
        counter: Counter[str] = Counter()
        last_reason: dict[str, str] = {}
        try:
            for line in REJECTED_WORDS_LOG_PATH.read_text(encoding="utf-8").splitlines()[-2000:]:
                parts = line.split("\t")
                if len(parts) < 6:
                    continue
                _, word, _base, _user_id, _name, reason = parts[:6]
                if reason in {"too_short", "letters_do_not_fit", "source_word"}:
                    continue
                if word in self.dictionary_words or word in self.approved_words or _known_dictionary_word(self, word):
                    continue
                counter[word] += 1
                last_reason[word] = reason
        except Exception:
            LOGGER.exception("Could not read rejected Lexicon words")
            return []
        return [(word, count, last_reason.get(word, "unknown")) for word, count in counter.most_common(limit)]


async def _is_chat_admin(app: Any, message: Message) -> bool:
    if message.from_user is None:
        return False
    try:
        member = await app.bot.get_chat_member(message.chat.id, message.from_user.id)
        return getattr(member, "status", None) in {"creator", "administrator"}
    except Exception:
        LOGGER.info("Could not check admin status for Lexicon command", exc_info=True)
        return False


def _parse_text_command(message: Message) -> tuple[str, str] | None:
    match = LEXICON_TEXT_COMMAND_RE.match((message.text or "").strip())
    if not match:
        return None
    return match.group(1).lower(), (match.group(2) or "").strip()


async def _reply_check(app: Any, service: LearningLexiconService, message: Message, raw_word: str) -> None:
    if not raw_word:
        await message.reply("Формат: <code>/lex_check слово</code>")
        return
    word = _stored_word(raw_word)
    strict_ok, _ = validate_lexicon_word(service, word, admin_override=False)
    admin_ok, _ = validate_lexicon_word(service, word, admin_override=True)
    status = "строго да" if strict_ok or word in service.approved_words else "админ может добавить" if admin_ok else "нет"
    await message.reply(f"📖 <code>{app.safe_output_text(word)}</code>: <b>{status}</b>")


async def _reply_add(app: Any, service: LearningLexiconService, message: Message, raw_word: str) -> None:
    if not await _is_chat_admin(app, message):
        await message.reply("📖 Добавлять слова в Лексикон могут только администраторы чата.")
        return
    if not raw_word:
        await message.reply("Формат: <code>/lex_add слово</code>")
        return
    ok, result = service.add_approved_word(raw_word, admin_override=True)
    if not ok:
        await message.reply(f"📖 Не добавил: {app.safe_output_text(result)}")
        return
    await message.reply(f"📖 Слово добавлено в словарь Лексикона: <code>{app.safe_output_text(result)}</code>")


async def _reply_pending(app: Any, service: LearningLexiconService, message: Message) -> None:
    if not await _is_chat_admin(app, message):
        await message.reply("📖 Очередь слов видят только администраторы чата.")
        return
    pending = service.pending_words()
    if not pending:
        await message.reply("📖 Очередь спорных слов пока пуста.")
        return
    lines = ["📖 <b>Спорные слова Лексикона</b>", "", "Добавить: <code>/lex_add слово</code>", ""]
    for word, count, reason in pending:
        lines.append(f"— <code>{app.safe_output_text(word)}</code> · {count} раз · {reason}")
    await message.reply("\n".join(lines))


def register_lexicon_learning_handlers(app: Any, service: LearningLexiconService) -> None:
    dispatcher = app.dp

    @dispatcher.message(F.text.regexp(LEXICON_TEXT_COMMAND_RE))
    async def lexicon_text_command_fallback(message: Message) -> None:
        if not app.is_group_chat(message) or not app.is_allowed_chat(message.chat.id):
            return
        parsed = _parse_text_command(message)
        if parsed is None:
            return
        command, argument = parsed
        print(
            f"LEXICON_COMMAND_HANDLED fallback=true command={command} chat_id={message.chat.id} thread_id={message.message_thread_id}",
            flush=True,
        )
        if command in {"lex_check", "lexicon_check"}:
            await _reply_check(app, service, message, argument)
            return
        if command in {"lex_add", "lexicon_add"}:
            await _reply_add(app, service, message, argument)
            return
        if command in {"lex_pending", "lexicon_pending"}:
            await _reply_pending(app, service, message)
            return

    dispatcher.message.handlers.insert(0, dispatcher.message.handlers.pop())

    @dispatcher.message(Command(commands=["lex_add", "lexicon_add"]))
    async def lexicon_add_word(message: Message) -> None:
        if not app.is_group_chat(message) or not app.is_allowed_chat(message.chat.id):
            return
        parts = (message.text or "").split(maxsplit=1)
        await _reply_add(app, service, message, parts[1] if len(parts) > 1 else "")

    dispatcher.message.handlers.insert(0, dispatcher.message.handlers.pop())

    @dispatcher.message(Command(commands=["lex_pending", "lexicon_pending"]))
    async def lexicon_pending_words(message: Message) -> None:
        if not app.is_group_chat(message) or not app.is_allowed_chat(message.chat.id):
            return
        await _reply_pending(app, service, message)

    dispatcher.message.handlers.insert(0, dispatcher.message.handlers.pop())

    @dispatcher.message(Command(commands=["lex_check", "lexicon_check"]))
    async def lexicon_check_word(message: Message) -> None:
        if not app.is_group_chat(message) or not app.is_allowed_chat(message.chat.id):
            return
        parts = (message.text or "").split(maxsplit=1)
        await _reply_check(app, service, message, parts[1] if len(parts) > 1 else "")

    dispatcher.message.handlers.insert(0, dispatcher.message.handlers.pop())
    print("LEXICON_LEARNING_READY approved_words=on rejected_queue=on admin_commands=on admin_override=on curated_common=on hunspell=lazy_optional command_fallback=on", flush=True)

"""Raccoon companion layer for the stable Lexicon service.

This module deliberately subclasses the production service instead of modifying its
storage, scoring, routing or dictionary internals.
"""

from __future__ import annotations

from typing import Any

from lexicon_learning import register_lexicon_learning_handlers
from lexicon_learning_persistent import LearningLexiconService as BaseLearningLexiconService
from lexicon_raccoon import RACCOON_WORD, raccoon_can_hide, raccoon_finder_name
from minigames import PlayerResult, WordGameRound


class LearningLexiconService(BaseLearningLexiconService):
    """Stable Lexicon service with a small per-round raccoon companion."""

    def _raccoon_line(self, round_data: WordGameRound, *, final: bool = False) -> str:
        finder_name = raccoon_finder_name(round_data)
        if finder_name is not None:
            return f"🦝 Енота нашел: <b>{self.app.safe_output_text(finder_name)}</b>"

        if raccoon_can_hide(round_data.base_word):
            if final:
                return "🦝 Енот прятался среди букв, но так и остался незамеченным."
            return "🦝 Где-то среди этих букв прячется енот. Кто его найдет?"

        return "🦝 Енот не обнаружен среди букв, но все равно играет с вами."

    def render_start(self, round_data: WordGameRound) -> str:
        rendered = super().render_start(round_data)
        return f"{rendered}\n\n{self._raccoon_line(round_data)}"

    def render_status(self, round_data: WordGameRound) -> str:
        rendered = super().render_status(round_data)
        return f"{rendered}\n\n{self._raccoon_line(round_data)}"

    def render_finish(self, round_data: WordGameRound, players: list[PlayerResult]) -> str:
        rendered = super().render_finish(round_data, players)
        return f"{rendered}\n\n{self._raccoon_line(round_data, final=True)}"

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
        if word == RACCOON_WORD:
            return (
                "🦝 <b>Енот найден!</b>\n\n"
                f"🦝 Енота нашел: <b>{self.app.safe_output_text(player.name)}</b>\n\n"
                f"<code>{RACCOON_WORD}</code>\n"
                f"+{points}🌟 · всего: <b>{total_points}🌟</b>\n"
                f"Слов у автора: <b>{len(player.words)}</b>\n"
                f"Страница заполнена на <b>{percent}%</b> — {found}/{total}"
            )

        return super().strong_found_reply(
            player=player,
            word=word,
            points=points,
            total_points=total_points,
            found=found,
            total=total,
            percent=percent,
        )


print(
    "LEXICON_RACCOON_READY word=енот start=on found_line=on finish=on absent_companion=on",
    flush=True,
)

__all__ = ["LearningLexiconService", "register_lexicon_learning_handlers"]

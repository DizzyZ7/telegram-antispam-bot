"""Local entertainment text generation."""

from __future__ import annotations

import random
import re
from collections import defaultdict

from .config import MAX_GENERATED_TOKENS, MIN_MESSAGES_TO_GENERATE

TOKEN_RE = re.compile(
    r"[A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9_'’-]*|[.,!?…:;]",
    re.UNICODE,
)
START_TOKEN = "<START>"
END_TOKEN = "<END>"


def tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall(text)


def _detokenize(tokens: list[str]) -> str:
    if not tokens:
        return ""

    no_space_before = {".", ",", "!", "?", "…", ":", ";"}
    result = ""
    for token in tokens:
        if not result:
            result = token
        elif token in no_space_before:
            result += token
        else:
            result += " " + token

    if result and result[-1] not in ".!?…":
        result += "."
    return result


def _normalized_for_comparison(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().casefold())


def generate_chat_text(
    messages: list[str],
    *,
    rng: random.Random | None = None,
    max_tokens: int = MAX_GENERATED_TOKENS,
) -> str | None:
    """Generate a new phrase from one chat without mixing data across chats."""
    rng = rng or random.Random()

    tokenized: list[list[str]] = []
    originals: set[str] = set()
    for message in messages:
        tokens = tokenize(message)
        if len(tokens) < 2:
            continue
        tokenized.append(tokens)
        originals.add(_normalized_for_comparison(_detokenize(tokens)))

    if len(tokenized) < MIN_MESSAGES_TO_GENERATE:
        return None

    transitions: dict[str, list[str]] = defaultdict(list)
    for tokens in tokenized:
        previous = START_TOKEN
        for token in tokens:
            transitions[previous].append(token)
            previous = token.casefold()
        transitions[previous].append(END_TOKEN)

    if not transitions.get(START_TOKEN):
        return None

    for _ in range(8):
        output: list[str] = []
        current = START_TOKEN

        for _step in range(max(4, int(max_tokens))):
            options = transitions.get(current)
            if not options:
                break
            token = rng.choice(options)
            if token == END_TOKEN:
                if len(output) >= 4:
                    break
                current = START_TOKEN
                continue
            output.append(token)
            current = token.casefold()

        generated = _detokenize(output).strip()
        normalized = _normalized_for_comparison(generated)
        if len(output) >= 4 and generated and normalized not in originals:
            return generated

    return None

"""Local context-aware entertainment text generation."""

from __future__ import annotations

import random
import re
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable

from .config import MAX_GENERATED_TOKENS, MIN_MESSAGES_TO_GENERATE

TOKEN_RE = re.compile(
    r"[A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9_'’-]*|[.,!?…:;]",
    re.UNICODE,
)
WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё]", re.UNICODE)
END_TOKEN = "<END>"
PUNCTUATION = frozenset({".", ",", "!", "?", "…", ":", ";"})

# These are intentionally small: the generator needs content words for topical
# focus, not a full linguistic stop-word dictionary.
CONTEXT_STOPWORDS = frozenset(
    {
        "и",
        "а",
        "но",
        "или",
        "что",
        "как",
        "в",
        "во",
        "на",
        "по",
        "с",
        "со",
        "к",
        "у",
        "за",
        "из",
        "до",
        "для",
        "от",
        "о",
        "об",
        "это",
        "тот",
        "эта",
        "этот",
        "там",
        "тут",
        "вот",
        "же",
        "бы",
        "ли",
        "не",
        "ни",
        "я",
        "мы",
        "ты",
        "вы",
        "он",
        "она",
        "они",
        "мне",
        "меня",
        "его",
        "ее",
        "их",
        "уже",
        "еще",
        "просто",
        "так",
        "то",
        "ну",
        "да",
    }
)

DANGLING_WORDS = frozenset(
    {
        "и",
        "а",
        "но",
        "или",
        "что",
        "как",
        "в",
        "во",
        "на",
        "по",
        "с",
        "со",
        "к",
        "у",
        "за",
        "из",
        "до",
        "для",
        "от",
        "о",
        "об",
        "если",
        "когда",
        "чтобы",
        "потому",
        "не",
        "ни",
    }
)

# Switching phrase continuation is much safer around these boundaries than in
# the middle of a noun/verb phrase. Short synthetic corpora may still use one
# non-boundary switch to preserve the old recombination contract.
PHRASE_BOUNDARY_WORDS = frozenset(
    {"и", "а", "но", "или", "что", "если", "когда", "потом", "зато", "потому", "чтобы", "пусть"}
)


def tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall(text)


def _word_tokens(tokens: Iterable[str]) -> list[str]:
    return [token.casefold() for token in tokens if WORD_RE.search(token)]


def _detokenize(tokens: list[str]) -> str:
    if not tokens:
        return ""

    result = ""
    for token in tokens:
        if not result:
            result = token
        elif token in PUNCTUATION:
            result += token
        else:
            result += " " + token

    if result and result[-1] not in ".!?…":
        result += "."
    return result


def _normalized_for_comparison(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text.strip().casefold())
    return normalized.rstrip(".!?… ")


def _content_terms(messages: Iterable[str]) -> set[str]:
    terms: set[str] = set()
    for message in messages:
        for token in _word_tokens(tokenize(message)):
            if len(token) >= 4 and token not in CONTEXT_STOPWORDS:
                terms.add(token)
    return terms


def _source_trigrams(entries: Iterable[list[str]]) -> set[tuple[str, str, str]]:
    result: set[tuple[str, str, str]] = set()
    for tokens in entries:
        words = _word_tokens(tokens)
        result.update(zip(words, words[1:], words[2:]))
    return result


def _supported_trigram_ratio(
    tokens: list[str],
    source_trigrams: set[tuple[str, str, str]],
) -> float:
    words = _word_tokens(tokens)
    trigrams = list(zip(words, words[1:], words[2:]))
    if not trigrams:
        return 0.0
    return sum(trigram in source_trigrams for trigram in trigrams) / len(trigrams)


def _build_transitions(
    entries: Iterable[list[str]],
) -> tuple[dict[str, list[str]], dict[tuple[str, str], list[str]]]:
    unigram: dict[str, list[str]] = defaultdict(list)
    bigram: dict[tuple[str, str], list[str]] = defaultdict(list)

    for tokens in entries:
        lowered = [token.casefold() for token in tokens]
        for index in range(1, len(tokens) + 1):
            next_token = tokens[index] if index < len(tokens) else END_TOKEN
            unigram[lowered[index - 1]].append(next_token)
            if index >= 2:
                bigram[(lowered[index - 2], lowered[index - 1])].append(next_token)

    return unigram, bigram


def _focused_entries(
    entries: list[list[str]],
    context_terms: set[str],
) -> list[list[str]]:
    if not context_terms:
        return entries

    focused = [
        tokens
        for tokens in entries
        if set(_word_tokens(tokens)) & context_terms
    ]
    # A narrow topical model is useful only when it has enough examples to
    # produce variety. Otherwise the whole topic corpus remains the fallback.
    return focused if len(focused) >= 20 else entries


def _candidate_is_sane(
    tokens: list[str],
    text: str,
    *,
    min_words: int,
    originals: set[str],
) -> bool:
    words = _word_tokens(tokens)
    if len(words) < min_words or len(words) > 24:
        return False
    if _normalized_for_comparison(text) in originals:
        return False
    if words[-1] in DANGLING_WORDS:
        return False
    if re.search(r":\s*\d+\s*[.!?…]?$", text):
        return False
    if any(words.count(word) >= 4 for word in set(words)):
        return False

    trigrams = list(zip(words, words[1:], words[2:]))
    if trigrams and len(trigrams) != len(set(trigrams)):
        return False
    return True


def generate_chat_text(
    messages: list[str],
    *,
    rng: random.Random | None = None,
    max_tokens: int = MAX_GENERATED_TOKENS,
    context_messages: list[str] | None = None,
    candidate_count: int = 32,
) -> str | None:
    """Generate one new phrase while preserving local chat phrasing.

    V2 deliberately avoids a one-word Markov chain. Most of a candidate follows
    observed two-token transitions. One controlled crossover may switch to a
    different observed continuation, preferably at punctuation/conjunction
    boundaries. Many candidates are generated and ranked by phrase support,
    recent-context overlap and basic structural quality.
    """

    rng = rng or random.Random()
    entries: list[list[str]] = []
    originals: set[str] = set()

    for message in messages:
        tokens = tokenize(message)
        if len(_word_tokens(tokens)) < 2:
            continue
        entries.append(tokens)
        originals.add(_normalized_for_comparison(_detokenize(tokens)))

    if len(entries) < MIN_MESSAGES_TO_GENERATE:
        return None

    recent_context = list(context_messages) if context_messages is not None else list(messages[-50:])
    context_terms = _content_terms(recent_context)
    context_vocabulary = {
        word
        for message in recent_context
        for word in _word_tokens(tokenize(message))
    }

    model_entries = _focused_entries(entries, context_terms)
    word_lengths = [len(_word_tokens(tokens)) for tokens in model_entries]
    min_words = 4 if statistics.median(word_lengths) <= 4 else 5
    source_trigrams = _source_trigrams(model_entries)
    unigram, bigram = _build_transitions(model_entries)

    first_token_counts = Counter(tokens[0].casefold() for tokens in model_entries if tokens)
    starts: list[list[str]] = []
    start_weights: list[float] = []
    for tokens in model_entries:
        if len(_word_tokens(tokens)) < min_words:
            continue
        # Preserve an interjection plus comma as one opening fragment. For
        # ordinary sentences two tokens are enough to anchor grammar while
        # still leaving room for recombination.
        start_size = 3 if len(tokens) >= 3 and tokens[1] in PUNCTUATION else min(2, len(tokens))
        start = tokens[:start_size]
        overlap = len(set(_word_tokens(tokens)) & context_terms)
        frequency = max(1, first_token_counts[tokens[0].casefold()])
        starts.append(start)
        start_weights.append((1.0 + 2.5 * overlap) / (frequency ** 0.35))

    if not starts:
        return None

    candidates: list[tuple[float, str]] = []
    attempts = max(80, max(8, int(candidate_count)) * 3)
    token_limit = max(6, int(max_tokens))

    for _attempt in range(attempts):
        output = list(rng.choices(starts, weights=start_weights, k=1)[0])
        crossed_over = False

        for _step in range(max(0, token_limit - len(output))):
            lowered = [token.casefold() for token in output]
            previous = lowered[-1]
            bigram_options = (
                bigram.get((lowered[-2], lowered[-1]), [])
                if len(lowered) >= 2
                else []
            )
            unigram_options = unigram.get(previous, [])

            bigram_non_terminal = [token for token in bigram_options if token != END_TOKEN]
            unigram_non_terminal = [token for token in unigram_options if token != END_TOKEN]
            bigram_values = {token.casefold() for token in bigram_non_terminal}
            crossover_options = [
                token
                for token in unigram_non_terminal
                if token.casefold() not in bigram_values
            ]

            at_boundary = output[-1] in PUNCTUATION or previous in PHRASE_BOUNDARY_WORDS
            short_corpus = min_words == 4
            next_token: str | None = None

            if not crossed_over and crossover_options:
                if at_boundary:
                    crossover_chance = 0.72
                elif short_corpus:
                    crossover_chance = 0.55
                else:
                    crossover_chance = 0.08
                if rng.random() < crossover_chance:
                    next_token = rng.choice(crossover_options)
                    crossed_over = True

            if next_token is None:
                if bigram_non_terminal:
                    next_token = rng.choice(bigram_non_terminal)
                elif unigram_non_terminal:
                    next_token = rng.choice(unigram_non_terminal)
                    crossed_over = True
                else:
                    break

            if (
                END_TOKEN in bigram_options
                and crossed_over
                and len(_word_tokens(output)) >= min_words
                and rng.random() < 0.60
            ):
                break

            output.append(next_token)

            if len(_word_tokens(output)) >= 18 and crossed_over and rng.random() < 0.35:
                break

        generated = _detokenize(output).strip()
        if not crossed_over or not _candidate_is_sane(
            output,
            generated,
            min_words=min_words,
            originals=originals,
        ):
            continue

        words = _word_tokens(output)
        supported_ratio = _supported_trigram_ratio(output, source_trigrams)
        context_overlap = len(set(words) & context_terms)
        content_words = {
            word
            for word in words
            if len(word) >= 4 and word not in CONTEXT_STOPWORDS
        }
        off_context = len(
            [
                word
                for word in content_words
                if context_vocabulary and word not in context_vocabulary
            ]
        )
        length_score = 1.0 - min(abs(len(words) - 10) / 14.0, 1.0)
        context_score = min(context_overlap / 2.0, 1.0) if context_terms else 0.5

        score = (
            3.2 * supported_ratio
            + 1.8 * context_score
            + 0.4 * length_score
            - 0.12 * min(off_context, 4)
            + rng.random() * 0.22
        )
        candidates.append((score, generated))

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0], reverse=True)
    top = candidates[: min(10, len(candidates))]
    # Do not always return the same mathematically best line. Sampling from the
    # high-quality band keeps the bot varied while preserving the reranker.
    weights = [1.0 / ((index + 1) ** 0.65) for index in range(len(top))]
    return rng.choices(top, weights=weights, k=1)[0][1]


def generate_text(
    request: "GenerationRequest",
    *,
    rng: random.Random,
    engine: str | None = None,
) -> "GenerationResult | None":
    """Compatibility facade for v3 with a one-release v2 rollback path."""

    from .config import resolve_generation_engine
    from .generation_v3 import GenerationResult, GenerationV3

    selected = resolve_generation_engine(engine)
    if selected == "v2":
        generated = generate_chat_text(
            request.source_messages,
            rng=rng,
            context_messages=request.context_messages,
        )
        if not generated:
            return None
        return GenerationResult(
            text=generated,
            engine="v2",
            score=0.0,
            candidate_count=1,
            rejection_counts={},
        )

    return GenerationV3().generate(request, rng=rng)

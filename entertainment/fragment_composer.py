"""Bounded grammatical remixes of learned human phrases, without story templates."""
from __future__ import annotations

import random
import re
from dataclasses import dataclass
from functools import lru_cache

from .coherence import generated_text_defect
from .generation_v3 import (GenerationMode, GenerationRequest, GenerationResult,
                            longest_contiguous_word_overlap, normalize_source_text)
from .language import COPULAR_LEMMAS, _morph, analyze_token, build_topic_anchor, short_predicative_parse
from .moderation_safety import is_safe_entertainment_reply, is_safe_entertainment_source
from .novelty import is_novel_generated_text

_WORDS = re.compile(r"[А-Яа-яЁё]+(?:-[А-Яа-яЁё]+)*")
_CLAUSE = re.compile(r"[.!?;,\n]+|\s+(?:и|а|но|зато)\s+", re.I)
_ANIMALS = frozenset({'кот', 'кошка', 'котенок', 'енот', 'собака', 'пес', 'сова',
                      'хомяк', 'лиса', 'пингвин', 'дракон', 'робот', 'персонаж',
                      'капибара', 'крокодил'})
_MAX_SOURCES = 128
_MAX_ATTEMPTS = 160


@lru_cache(maxsize=4096)
def _parses(word: str):
    return tuple(_morph().parse(word))


@lru_cache(maxsize=4096)
def _parse(word: str):
    return _parses(word)[0]


def _contains_name(text: str) -> bool:
    words = _WORDS.findall(text)
    for index, word in enumerate(words):
        parses = _parses(word.casefold())
        if not any(p.is_known and {'Name', 'Surn', 'Patr'} & p.tag.grammemes for p in parses):
            continue
        # A lower-case abstract noun explicitly qualified by an adjective can
        # be unambiguous enough: «новый роман», «последняя надежда».
        if (word.islower() and index and _parse(words[index - 1].casefold()).tag.POS == 'ADJF'
                and any(p.tag.POS == 'NOUN' and p.tag.animacy == 'inan' for p in parses)):
            continue
        return True
    return False


@dataclass(frozen=True)
class _Subject:
    words: tuple[str, ...]
    number: str
    gender: str | None
    source: str


@dataclass(frozen=True)
class _Predicate:
    words: tuple[str, ...]
    verb_index: int
    source: str


def _fragments(source: str) -> list[tuple[_Subject | None, _Predicate]]:
    if (not is_safe_entertainment_source(source) or '@' in source
            or re.search(r'https?://|www\.|t\.me/', source, re.I)):
        return []
    identity = normalize_source_text(source)
    output = []
    for clause in _CLAUSE.split(source)[:8]:
        if re.search(r'[A-Za-z0-9]', clause) or _contains_name(clause):
            continue
        words = tuple(w.casefold().replace('ё', 'е') for w in _WORDS.findall(clause))
        if not 3 <= len(words) <= 20:
            continue
        parses = [_parse(w) for w in words]
        verb_index = next((i for i, p in enumerate(parses)
                           if p.tag.POS in {'VERB', 'ADJS'}
                           or (i and parses[i - 1].tag.POS == 'NPRO'
                               and short_predicative_parse(words[i]))), None)
        if verb_index is None:
            continue
        # Personal names/roles and unresolved pronoun references must not turn
        # into the target or object of a new, accidentally hostile assertion.
        if any((p.tag.POS == 'NOUN' and p.tag.animacy == 'anim'
                   and p.normal_form not in _ANIMALS
                   and not any(q.tag.POS == 'NOUN' and q.tag.animacy == 'inan'
                               for q in _parses(words[i])))
               or (p.tag.POS == 'NPRO' and p.normal_form != 'себя'
                   and not (i < verb_index and p.normal_form in {'я', 'мы'}))
               for i, p in enumerate(parses)):
            continue
        nominatives = {
            i: next((p for p in _parses(words[i])
                     if p.tag.POS == 'NOUN' and p.tag.case == 'nomn'
                     and (p.tag.animacy == 'inan' or p.normal_form in _ANIMALS)
                     and p.normal_form == parses[i].normal_form), None)
            for i in range(verb_index)
        }
        head = next((i for i in range(verb_index - 1, -1, -1) if nominatives[i]), None)
        subject = None
        if head is not None:
            parsed_subject = nominatives[head]
            number, gender = parsed_subject.tag.number, parsed_subject.tag.gender
            if number not in {'sing', 'plur'} or (number == 'sing' and gender not in {'masc', 'femn', 'neut'}):
                continue
            start = head
            if head and parses[head - 1].tag.POS == 'ADJF' and parses[head - 1].tag.case == 'nomn':
                start -= 1
            subject = _Subject(words[start:head + 1], str(number),
                               str(gender) if gender else None, identity)
            body_start = head + 1
        else:
            # Human first-person and subjectless chat sentences can teach
            # actions; they never supply a human target for the remix.
            person = next((i for i, p in enumerate(parses[:verb_index])
                           if p.tag.POS == 'NPRO'), None)
            if any(p.tag.POS == 'NOUN' for p in parses[:verb_index]):
                continue
            body_start = person + 1 if person is not None else 0
        body = words[body_start:]
        if not 2 <= len(body) <= 7:
            continue
        if sum(_parse(w).tag.POS == 'VERB' for w in body) > 1:
            continue
        # Do not truncate a long predicate just to fit a word budget.
        if generated_text_defect(' '.join(words)):
            continue
        output.append((subject, _Predicate(body, verb_index - body_start, identity)))
    return output


def _agree(predicate: _Predicate, subject: _Subject) -> str | None:
    words = list(predicate.words)
    verb = _parse(words[predicate.verb_index])
    if verb.tag.POS not in {'VERB', 'ADJS'}:
        verb = short_predicative_parse(words[predicate.verb_index])
        if verb is None:
            return None
    forms = {subject.number}
    if verb.tag.tense == 'past' or verb.tag.POS == 'ADJS':
        if subject.number == 'sing':
            forms.add(subject.gender)
    else:
        forms.add('3per')
    inflected = verb.inflect(forms)
    if inflected is None:
        return None
    words[predicate.verb_index] = inflected.word
    # A predicate can carry its own subject-dependent short adjective or an
    # instrumental adjective after a copula. Object modifiers stay attached
    # to their noun; uncertain bare complements fail closed.
    agreement = {subject.number}
    if subject.number == 'sing':
        agreement.add(subject.gender)
    for index in range(predicate.verb_index + 1, len(words)):
        parsed = _parse(words[index])
        if verb.normal_form in COPULAR_LEMMAS:
            parsed = short_predicative_parse(words[index]) or parsed
        if parsed.tag.POS in {'ADJS', 'PRTS'}:
            agreed = parsed.inflect(agreement)
        elif parsed.tag.POS == 'ADJF' and parsed.tag.case == 'ablt':
            if any(_parse(w).tag.POS == 'NOUN' and _parse(w).tag.case == 'ablt'
                   for w in words[index + 1:]):
                continue
            if verb.normal_form not in COPULAR_LEMMAS:
                return None
            agreed = parsed.inflect(agreement | {'ablt'})
        else:
            continue
        if agreed is None:
            return None
        words[index] = agreed.word
    return ' '.join(words)


def reject_replayed_memory(text: str, sources: list[str], recent_outputs: list[str],
                           fresh_messages: list[str]) -> bool:
    """One outgoing contract for learned composition and legacy generation."""
    if not is_safe_entertainment_reply(text) or generated_text_defect(text):
        return True
    if _contains_name(text):
        return True
    if not is_novel_generated_text(text, sources, recent_outputs):
        return True
    size = len(normalize_source_text(text).split())
    if size < 4 or size > 24:
        return True
    for source in dict.fromkeys(sources):
        overlap = longest_contiguous_word_overlap(text, source)
        if overlap > 6 or overlap / size > .70:
            return True
    lemma_text = ' '.join(analyze_token(w).lemma for w in normalize_source_text(text).split())
    for fresh in dict.fromkeys(fresh_messages):
        if longest_contiguous_word_overlap(text, fresh) >= 4:
            return True
        lemma_fresh = ' '.join(analyze_token(w).lemma for w in normalize_source_text(fresh).split())
        if longest_contiguous_word_overlap(lemma_text, lemma_fresh) >= 4:
            return True
    return False


def compose_memory_text(request: GenerationRequest, *, rng: random.Random) -> GenerationResult | None:
    """Combine subjects and complete predicates with distinct source identities."""
    sources = list(dict.fromkeys(request.source_messages))
    if len({normalize_source_text(s) for s in sources}) < 2:
        return None
    anchor = build_topic_anchor(request.context_messages, trigger_text=request.trigger_text,
                                direct_reply=request.mode is GenerationMode.DIRECT_REPLY)
    # Sample across the accumulated pool; a current theme is only a preference.
    pool = list(sources)
    rng.shuffle(pool)
    pool = sorted(pool, key=lambda s: anchor.relevance(normalize_source_text(s).split()),
                  reverse=True)[:_MAX_SOURCES]
    fragments = [fragment for source in pool for fragment in _fragments(source)]
    subjects = list(dict.fromkeys(s for s, _ in fragments if s is not None))
    predicates = list(dict.fromkeys(p for _, p in fragments))
    if not subjects or not predicates:
        return None
    accepted: dict[str, float] = {}
    for _ in range(_MAX_ATTEMPTS):
        subject = rng.choice(subjects)
        options = [p for p in predicates if p.source != subject.source]
        if not options:
            continue
        first = rng.choice(options)
        body = _agree(first, subject)
        if body is None:
            continue
        others = [p for p in options if p.source != first.source]
        if others and rng.random() < .70:
            second = _agree(rng.choice(others), subject)
            if second is not None:
                body += ' и ' + second
        text = (' '.join(subject.words) + ' ' + body).capitalize() + '.'
        if reject_replayed_memory(text, sources, request.recent_bot_outputs, []):
            continue
        score = anchor.relevance(normalize_source_text(text).split()) + rng.random() * .35
        accepted[text] = score
        if len(accepted) >= 32:
            break
    if not accepted:
        return None
    ranked = sorted(accepted, key=accepted.get, reverse=True)[:12]
    text = rng.choice(ranked)
    return GenerationResult(text=text, engine='v3', score=4 + accepted[text],
                            candidate_count=len(accepted), rejection_counts={}, origin='memory')

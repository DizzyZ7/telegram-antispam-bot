"""Bounded grammatical remixes of learned human phrases, without story templates."""
from __future__ import annotations

import random
import re
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache

from .coherence import generated_text_defect
from .generation_v3 import (GenerationMode, GenerationRequest, GenerationResult,
                            longest_contiguous_word_overlap, normalize_source_text)
from .language import (COPULAR_LEMMAS, DATIVE_INFINITIVE_LEMMAS, _morph, analyze_token, build_topic_anchor,
                       copula_complement_parse, short_predicative_parse)
from .moderation_safety import is_safe_entertainment_reply, is_safe_entertainment_source
from .novelty import is_novel_generated_text

_WORDS = re.compile(r"[А-Яа-яЁё]+(?:-[А-Яа-яЁё]+)*")
_CLAUSE = re.compile(r"[.!?;,\n]+|\s+(?:и|а|но|зато)\s+", re.I)
_ANIMALS = frozenset({'кот', 'кошка', 'котенок', 'енот', 'собака', 'пес', 'сова',
                      'хомяк', 'лиса', 'пингвин', 'дракон', 'робот', 'персонаж',
                      'капибара', 'крокодил'})
_CHAPTER_OBJECT_VERBS = frozenset({'писать', 'написать', 'дописать', 'дописывать',
                                 'читать', 'прочитать', 'дочитать', 'перечитать',
                                 'редактировать', 'опубликовать', 'публиковать'})
_MAX_SOURCES = 128
_MAX_ATTEMPTS = 160
_MAX_SUBJECTS_PER_SOURCE = 12


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


def _safe_clauses(source: str, *, min_words: int) -> Iterator[tuple[tuple[str, ...], tuple]]:
    if (not is_safe_entertainment_source(source) or '@' in source
            or source.lstrip().startswith('/')
            or re.search(r'https?://|www\.|t\.me/', source, re.I)):
        return
    for clause in _CLAUSE.split(source)[:8]:
        if re.search(r'[A-Za-z0-9]', clause) or _contains_name(clause):
            continue
        words = tuple(w.casefold().replace('ё', 'е') for w in _WORDS.findall(clause))
        if min_words <= len(words) <= 20:
            yield words, tuple(_parse(w) for w in words)


def _matching_adjective(word: str, noun):
    return next((p for p in _parses(word)
                 if p.tag.POS == 'ADJF' and p.tag.case == noun.tag.case
                 and p.tag.number == noun.tag.number
                 and (noun.tag.number == 'plur' or p.tag.gender == noun.tag.gender)), None)


def _human_role_reading(words: tuple[str, ...], parses: tuple, index: int) -> bool:
    alternatives = _parses(words[index])
    if not any(p.is_known and p.tag.POS == 'NOUN' and p.tag.animacy == 'anim'
               and p.normal_form not in _ANIMALS
               and not {'Name', 'Surn', 'Patr'} & p.tag.grammemes for p in alternatives):
        return False
    # «Дописываю главу» identifies a direct book object. A recipient or a
    # prepositional phrase («писал главе», «писал про главу») stays ambiguous.
    if any(p.is_known and p.normal_form == 'глава' and p.tag.animacy == 'inan'
           and p.tag.case == 'accs' for p in alternatives):
        for previous in range(index - 1, -1, -1):
            if parses[previous].tag.POS in {'VERB', 'INFN'}:
                return not (parses[previous].normal_form in _CHAPTER_OBJECT_VERBS
                            and all(p.tag.POS in {'ADJF', 'ADVB', 'PRCL'}
                                    for p in parses[previous + 1:index]))
    return True


def _impersonal_infinitive(words: tuple[str, ...]) -> bool:
    for index, word in enumerate(words):
        parsed = _parse(word)
        if 'Prdx' not in parsed.tag.grammemes and parsed.tag.POS != 'PRED':
            continue
        for following in words[index + 1:]:
            pos = _parse(following).tag.POS
            if pos == 'INFN':
                return True
            if pos not in {'ADVB', 'PRCL'}:
                break
    return False


def _vocabulary_subjects(source: str) -> list[_Subject]:
    """Learn safe object phrases even when a human was the implied subject."""
    identity = normalize_source_text(source)
    subjects: dict[_Subject, None] = {}
    for words, parses in _safe_clauses(source, min_words=1):
        for index, best in enumerate(parses):
            modified = index > 0 and any(p.tag.POS == 'ADJF' for p in _parses(words[index - 1]))
            if best.tag.POS != 'NOUN' and not modified:
                continue
            # In «с синей клавиатурой», «синей» is an agreed adjective,
            # although the dictionary's first parse is the noun «синь».
            if index + 1 < len(words) and any(
                p.is_known and p.tag.POS == 'NOUN' and _matching_adjective(words[index], p)
                for p in _parses(words[index + 1])
            ):
                continue
            # «Очень рады» is still a short predicate without an explicit
            # pronoun/copula. Only a compatible nominal modifier can resolve it.
            if (short_predicative_parse(words[index]) is not None
                    and (not modified or any(
                        p.tag.POS == 'NPRO' or p.normal_form in COPULAR_LEMMAS
                        for p in parses[:index]))):
                continue
            # An ambiguous «пила» in «я пила кофе» is not a learned saw.
            if (any(p.tag.POS == 'VERB' for p in _parses(words[index]))
                    and (any(p.tag.POS == 'NPRO' for p in parses[:index])
                         or (index == 0 and any(p.tag.POS == 'NOUN' for p in parses[1:])))):
                continue
            noun = next((p for p in _parses(words[index])
                         if p.is_known and p.tag.POS == 'NOUN'
                         and (p.tag.animacy == 'inan' or p.normal_form in _ANIMALS)
                         and p.tag.number in {'sing', 'plur'}
                         and (not modified or _matching_adjective(words[index - 1], p))), None)
            if noun is None or _human_role_reading(words, parses, index):
                continue
            number, gender = noun.tag.number, noun.tag.gender
            if number == 'sing' and gender not in {'masc', 'femn', 'neut'}:
                continue
            nominal = noun.inflect({'nomn', str(number)})
            if nominal is None:
                continue
            phrase = [nominal.word]
            if modified:
                adjective = _matching_adjective(words[index - 1], noun)
                forms = {'nomn', str(number)}
                if number == 'sing':
                    forms.add(str(gender))
                agreed = adjective.inflect(forms)
                if agreed is None:
                    continue
                phrase.insert(0, agreed.word)
            subject = _Subject(tuple(phrase), str(number), str(gender) if gender else None, identity)
            subjects[subject] = None
            if len(subjects) >= _MAX_SUBJECTS_PER_SOURCE:
                return list(subjects)
    return list(subjects)


def _fragments(source: str) -> list[tuple[_Subject | None, _Predicate]]:
    identity = normalize_source_text(source)
    output = []
    for words, parses in _safe_clauses(source, min_words=2):
        verb_index = next((i for i, p in enumerate(parses)
                           if p.tag.POS == 'VERB'
                           or ((p.tag.POS == 'ADJS'
                                or (i and parses[i - 1].tag.POS == 'NPRO' and p.tag.POS == 'NOUN'))
                               and copula_complement_parse(
                                   words[i], words[i + 1:]
                               ).tag.POS in {'ADJS', 'PRTS'})), None)
        if verb_index is None:
            continue
        # Personal names/roles and unresolved pronoun references must not turn
        # into the target or object of a new, accidentally hostile assertion.
        if any((p.tag.POS == 'NOUN' and _human_role_reading(words, parses, i))
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
        main = _parse(body[verb_index - body_start])
        if (any(_parse(w).tag.POS == 'INFN' for w in body)
                and (main.normal_form in DATIVE_INFINITIVE_LEMMAS
                     or (main.normal_form in COPULAR_LEMMAS and _impersonal_infinitive(body)))):
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
            parsed = copula_complement_parse(words[index], tuple(words[index + 1:]))
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
    subjects = list(dict.fromkeys([
        *(s for s, _ in fragments if s is not None),
        *(s for source in pool for s in _vocabulary_subjects(source)),
    ]))
    predicates = list(dict.fromkeys(p for _, p in fragments))
    if not subjects or not predicates:
        return None
    predicate_keys = {p: tuple(analyze_token(w).lemma.replace('ё', 'е') for w in p.words)
                      for p in predicates}
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
        others = [p for p in options if p.source != first.source
                  and predicate_keys[p] != predicate_keys[first]]
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

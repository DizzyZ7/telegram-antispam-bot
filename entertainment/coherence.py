"""Tiny conservative quality gate for locally recombined Fosgen chat text.

This only inspects *bot-generated* candidates, never censors humans or
removes chat memory. It rejects a few observable mechanical pathologies
rather than pretending to understand semantics without a language model.
"""

from __future__ import annotations

import re
from functools import lru_cache

_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё'-]*")
_FAKE_INTERNAL_STATUS = re.compile(
    r"\b(?:лимит\w*|квот\w*)\s+(?:генера\w*|токен\w*|ответ\w*)\b"
    r"|\b(?:чат|бот|сервер)\s+(?:достиг\w*|исчерпал\w*)\s+(?:лимит\w*|квот\w*)\b",
    re.IGNORECASE,
)
_BROKEN_SWITCH = re.compile(
    r"\bа\s+нет\s+(?:утро|вечер|день|чат|бот)\b",
    re.IGNORECASE,
)
# Unintentional duplicated content stems: лимита лимит, генерации генерация.
# Benign rhetorical repetitions are tolerated only for a small authored list.
_RHETORICAL = frozenset({"очень", "прямо", "скоро", "ладно", "даже"})
_DANGLING = frozenset({'и', 'а', 'но', 'или', 'что', 'чтобы', 'если', 'когда',
                       'на', 'в', 'во', 'к', 'ко', 'с', 'со', 'из', 'для', 'у',
                       'от', 'за', 'под', 'перед', 'про', 'через', 'по', 'потому'})


@lru_cache(maxsize=4096)
def _tag(word: str):
    from .language import _morph
    return _morph().parse(word)[0].tag


@lru_cache(maxsize=4096)
def _unambiguous_nominative(word: str) -> bool:
    from .language import _morph
    nouns = [p for p in _morph().parse(word) if p.is_known and p.tag.POS == 'NOUN']
    return (any(p.tag.case == 'nomn' for p in nouns)
            and not any(p.tag.case in {'accs', 'datv'} for p in nouns))


def _repeated_predicate_defect(text: str) -> bool:
    from .language import analyze_token
    for sentence in re.split(r'[.!?;]+', text):
        parts = re.split(r'\s+и\s+', sentence, flags=re.I)
        for left, right in zip(parts, parts[1:]):
            left_words = [w.casefold() for w in _WORD_RE.findall(left)]
            right_words = [w.casefold() for w in _WORD_RE.findall(right)]
            if len(right_words) < 2 or len(left_words) < len(right_words):
                continue
            if not any(_tag(w).POS in {'VERB', 'ADJS'} for w in right_words):
                continue
            # «Не ушел ... и ушел ...» is a contrast, not an identical action.
            if ('не' in left_words) != ('не' in right_words):
                continue
            left_lemmas = [analyze_token(w).lemma.replace('ё', 'е') for w in left_words]
            right_lemmas = [analyze_token(w).lemma.replace('ё', 'е') for w in right_words]
            if left_lemmas[-len(right_lemmas):] == right_lemmas:
                return True
    return False


def _past_agreement_defect(text: str) -> bool:
    from .language import COPULAR_LEMMAS, DATIVE_INFINITIVE_LEMMAS, analyze_token, copula_complement_parse
    # Only a clear, simple pre-verbal subject. Post-verbal subjects, coordinated
    # nouns and unknown morphology are deliberately left to the constructor.
    for clause in re.split(r'[.!?;,]+|\s+(?:а|но|зато)\s+', text, flags=re.I):
        words = [w.casefold() for w in _WORD_RE.findall(clause)]
        tags = [_tag(w) for w in words]
        for index, tag in enumerate(tags):
            if (tag.POS == 'VERB' and analyze_token(words[index]).lemma in DATIVE_INFINITIVE_LEMMAS
                    and any(t.POS == 'INFN' for t in tags[index + 1:])
                    and not any(t.case == 'datv' for t in tags[:index])
                    and any(_unambiguous_nominative(w) for w in words[:index])):
                return True
            if tag.POS != 'VERB' or tag.tense != 'past':
                continue
            if any(w in {'и', 'или'} for w in words[:index]):
                continue
            if any(t.POS == 'NOUN' and t.case == 'nomn' for t in tags[index + 1:]):
                continue
            subject = next((t for t in reversed(tags[:index])
                            if t.POS == 'NOUN' and t.case == 'nomn'), None)
            if subject is None:
                continue
            if subject.number and tag.number and subject.number != tag.number:
                return True
            if (subject.number == tag.number == 'sing' and subject.gender and tag.gender
                    and subject.gender != tag.gender):
                return True
            for position in range(index + 1, len(tags)):
                dependent = tags[position]
                if analyze_token(words[index]).lemma in COPULAR_LEMMAS:
                    dependent = copula_complement_parse(
                        words[position], tuple(words[position + 1:])
                    ).tag
                if dependent.POS in {'ADJS', 'PRTS'}:
                    pass
                elif (dependent.POS == 'ADJF' and dependent.case == 'ablt'
                      and analyze_token(words[index]).lemma in COPULAR_LEMMAS
                      and not any(t.POS == 'NOUN' and t.case == 'ablt' for t in tags[position + 1:])):
                    pass
                else:
                    continue
                if subject.number and dependent.number and subject.number != dependent.number:
                    return True
                if (subject.number == dependent.number == 'sing'
                        and subject.gender and dependent.gender and subject.gender != dependent.gender):
                    return True
    return False


def generated_text_defect(text: str | None) -> str | None:
    """Return a reason for a reliably observable low-quality output."""
    if not isinstance(text, str) or not text.strip():
        return "empty"
    if _FAKE_INTERNAL_STATUS.search(text):
        return "fake_internal_status"
    if _BROKEN_SWITCH.search(text):
        return "broken_clause_join"

    words = [word.casefold().replace("ё", "е") for word in _WORD_RE.findall(text)]
    if words and words[-1] in _DANGLING:
        return 'dangling_clause'
    if _repeated_predicate_defect(text):
        return 'repeated_predicate'
    if _past_agreement_defect(text):
        return 'subject_agreement'
    for left, right in zip(words, words[1:]):
        if (
            len(left) >= 4
            and len(right) >= 4
            and left[:5] == right[:5]
            and left not in _RHETORICAL
        ):
            return "adjacent_word_stems"

    return None


__all__ = ["generated_text_defect"]

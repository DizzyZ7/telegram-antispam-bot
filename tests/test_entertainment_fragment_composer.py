import random
import re
import unittest

from entertainment.fragment_composer import compose_memory_text, reject_replayed_memory
from entertainment.generation_v3 import (GenerationMode, GenerationRequest, build_transition_model, distinct_source_identities,
                                        longest_contiguous_word_overlap)
from entertainment.novelty import is_novel_generated_text
from entertainment.moderation_safety import is_safe_entertainment_reply


SOURCES = [
    'Старый чайник требует официальный выходной',
    'Домашний будильник уехал на дальнюю дачу',
    'Сонное вдохновение охраняет последний пельмень',
    'Голодные коты объявили тихий перерыв',
]


def request(sources=SOURCES, recent=()):
    return GenerationRequest(source_messages=list(sources), context_messages=['Сегодня вспоминаем чайник'],
                             trigger_text=None, mode=GenerationMode.AUTONOMOUS,
                             recent_bot_outputs=list(recent))


class LearnedFragmentTests(unittest.TestCase):
    def test_russian_yo_does_not_disguise_source_or_bot_replay(self):
        before = 'Ленивый будильник ушел отдыхать'
        after = 'Ленивый будильник ушёл отдыхать.'
        self.assertTrue(reject_replayed_memory(after, [before], [], []))
        self.assertFalse(is_novel_generated_text(after, [], [before]))
        self.assertEqual(longest_contiguous_word_overlap(after, before), 4)
        self.assertEqual(len(distinct_source_identities([before, after])), 1)
        model = build_transition_model(['Ёж уходит отдыхать'])
        self.assertEqual(model.next_options(['Еж']), model.next_options(['Ёж']))

    def test_learned_actions_produce_new_safe_phrases_without_news_catalogue(self):
        results = [compose_memory_text(request(), rng=random.Random(seed)) for seed in range(8)]
        self.assertTrue(all(results))
        texts = [r.text for r in results]
        self.assertGreater(len(set(texts)), 3)
        for text in texts:
            self.assertTrue(is_safe_entertainment_reply(text))
            self.assertNotIn(text.rstrip('.'), SOURCES)
            self.assertNotRegex(text.casefold(), r'комисси|собрани|департамент|срочные новости|эксперт')
            self.assertRegex(text.casefold(), r'требу|уеха|охраня|объяви')

    def test_irregular_past_agrees_with_a_new_feminine_subject(self):
        sources = ['Тихая кастрюля устроила маленький перерыв',
                   'Старый будильник запустил новый эксперимент',
                   'Ленивый чайник ушел на кухню']
        outputs = [r.text for seed in range(30)
                   if (r := compose_memory_text(request(sources), rng=random.Random(seed)))]
        feminine = [s for s in outputs if s.casefold().startswith('тихая кастрюля')]
        self.assertTrue(feminine)
        self.assertTrue(any('ушла' in s or 'запустила' in s for s in feminine))
        for text in feminine:
            self.assertNotRegex(text, r'\b(?:ушел|запустил|устроил)\b')

    def test_does_not_invent_sources_or_use_named_humans_as_subjects(self):
        for sources in [[], ['Чайник требует выходной'] * 40,
                        ['Маша требует официальный выходной', 'Иван охраняет последний пельмень']]:
            self.assertIsNone(compose_memory_text(request(sources), rng=random.Random(1)))

    def test_ambiguous_neuter_nominative_still_supplies_a_subject(self):
        sources = ['Сонное вдохновение давно ушло отдыхать',
                   'Светлое вдохновение готовилось работать',
                   'Тихое вдохновение успело поесть']
        result = compose_memory_text(request(sources), rng=random.Random(5))
        self.assertIsNotNone(result)
        self.assertRegex(result.text, r'(?:ушло|готовилось|успело)')

    def test_first_person_human_messages_supply_actions_without_becoming_targets(self):
        result = compose_memory_text(request([
            'Дедлайн требует официальный выходной',
            'Я опять забыл поесть',
            'Мы давно просили законный перерыв',
        ]), rng=random.Random(6))
        self.assertIsNotNone(result)
        self.assertTrue(result.text.startswith('Дедлайн'))
        self.assertRegex(result.text, r'забыл|просил')
        self.assertNotRegex(result.text.casefold(), r'\b(?:я|мы)\b')

    def test_fresh_four_word_fragment_is_rejected_even_inside_a_new_sentence(self):
        self.assertTrue(reject_replayed_memory(
            'Чайник опять забыл поесть сегодня и требует выходной.', SOURCES, [],
            ['Я опять забыл поесть сегодня']))

    def test_full_old_sentence_cannot_be_hidden_inside_a_longer_remix(self):
        old = 'Будильник уехал на очень далекую дачу после работы'
        self.assertTrue(reject_replayed_memory(
            old + ' и чайник объявил совершенно новый перерыв.', [old, *SOURCES], [], []))

    def test_named_person_is_not_a_target_in_legacy_remixes(self):
        self.assertTrue(reject_replayed_memory(
            'Иван опять испортил весь вечер.', SOURCES, [], []))

    def test_recent_bot_replies_are_not_replayed(self):
        first = compose_memory_text(request(), rng=random.Random(2))
        self.assertIsNotNone(first)
        second = compose_memory_text(request(recent=[first.text]), rng=random.Random(2))
        self.assertIsNotNone(second)
        self.assertNotEqual(first.text, second.text)

    def test_subject_dependent_short_complements_follow_new_gender_and_number(self):
        sources = ['Тихая кастрюля тихо работает',
                   'Я был совершенно готов работать', 'Мы были очень рады']
        result = compose_memory_text(request(sources), rng=random.Random(0))
        self.assertIsNotNone(result)
        self.assertNotRegex(result.text, r'\b(?:готов|рады)\b')
        self.assertRegex(result.text, r'готова|рада')

    def test_degree_adverb_before_short_adjective_does_not_get_gender_inflected(self):
        for speech in ('Я был совершенно готов работать', 'Я совершенно готов работать'):
            with self.subTest(speech=speech):
                sources = ['Тихая кастрюля тихо работает', speech, 'Мы были очень рады']
                texts = [r.text for seed in range(8)
                         if (r := compose_memory_text(request(sources), rng=random.Random(seed)))]
                self.assertTrue(any('готова' in s for s in texts))
                for text in texts:
                    if 'готова' in text:
                        self.assertIn('совершенно готова', text)
                    self.assertNotRegex(text, r'совершенна\b|совершенен\b|совершенны\b')

    def test_instrumental_predicative_adjectives_agree_with_feminine_subject(self):
        sources = ['Старая кастрюля осталась голодной',
                   'Ленивый будильник оказался готовым', 'Ночное ведро стало счастливым']
        feminine = [r.text for seed in range(12)
                    if (r := compose_memory_text(request(sources), rng=random.Random(seed)))
                    and r.text.startswith('Старая кастрюля')]
        self.assertTrue(feminine)
        for text in feminine:
            self.assertNotRegex(text, r'готовым|счастливым|голодным')

    def test_ambiguous_names_do_not_supply_subjects_or_pass_the_output_gate(self):
        sources = ['Надежда попросила дополнительный выходной',
                   'Старый будильник опять забыл выключиться',
                   'Сонная кастрюля потеряла всякий смысл']
        for seed in range(10):
            result = compose_memory_text(request(sources), rng=random.Random(seed))
            if result:
                self.assertNotIn('надежда', result.text.casefold())
        self.assertTrue(reject_replayed_memory(
            'Надежда опять забыла выключиться.', sources, [], []))

    def test_qualified_abstract_homonym_remains_usable_as_a_book(self):
        result = compose_memory_text(request([
            'Новый роман требует официальный выходной',
            'Я опять забыл поесть', 'Мы давно просили законный перерыв',
        ]), rng=random.Random(6))
        self.assertIsNotNone(result)
        self.assertTrue(result.text.startswith('Новый роман'))

    def test_inflection_does_not_hide_a_fresh_four_word_echo(self):
        self.assertTrue(reject_replayed_memory(
            'Дедлайн опять забыл поесть сегодня и требует выходной.', SOURCES, [],
            ['Мы опять забыли поесть сегодня']))

    def test_numbers_are_not_silently_dropped_from_learned_predicates(self):
        self.assertIsNone(compose_memory_text(request([
            'Тихая кастрюля требует официальный выходной',
            'Я прочитал 12 книг', 'Мы съели 7 пельменей',
        ]), rng=random.Random(0)))

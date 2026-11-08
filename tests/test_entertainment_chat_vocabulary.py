"""Ordinary accumulated speech supplies characters without seed templates."""
import random
import unittest

from entertainment.fragment_composer import compose_memory_text
from entertainment.generation_v3 import GenerationMode, GenerationRequest, normalize_source_text


ORDINARY = [
    'Поставил чайник на плиту',
    'Дописываю последнюю главу',
    'Купил свежие пельмени',
    'Ищу старый будильник',
    'Забыл выключить телевизор',
    'Открыл маленький холодильник',
]


def generate(sources, seed=4):
    return compose_memory_text(GenerationRequest(
        list(sources), [], None, GenerationMode.AUTONOMOUS, []), rng=random.Random(seed))


class OrdinaryVocabularyTests(unittest.TestCase):
    def test_ordinary_subjectless_messages_supply_varied_new_replies(self):
        results = [generate(ORDINARY, seed) for seed in range(12)]
        self.assertTrue(all(results))
        texts = [r.text for r in results]
        self.assertGreaterEqual(len(set(texts)), 6)
        originals = {normalize_source_text(s) for s in ORDINARY}
        for text in texts:
            self.assertNotIn(normalize_source_text(text), originals)
            self.assertNotRegex(text.casefold(), r'комисси|срочные новости|енот|огурец')

    def test_first_person_sources_do_not_require_personified_objects(self):
        for seed in range(4):
            result = generate(['Я ' + s.casefold() for s in ORDINARY], seed)
            self.assertIsNotNone(result)
            self.assertNotRegex(result.text.casefold(), r'\b(?:я|мы|ты|вы)\b')

    def test_object_case_and_modifier_become_a_correct_nominative_phrase(self):
        for phrase, expected in [
            ('о холодной сковороде', 'Холодная сковорода'),
            ('с синей клавиатурой', 'Синяя клавиатура'),
            ('купил маленькую кастрюлю', 'Маленькая кастрюля'),
        ]:
            with self.subTest(phrase=phrase):
                result = generate([phrase, 'Я снова хочу спать', 'Я давно забыл поесть'])
                self.assertIsNotNone(result)
                self.assertTrue(result.text.startswith(expected), result.text)
                self.assertNotRegex(result.text, r'\b(?:забыл|забыло)\b')

    def test_plural_object_keeps_plural_agreement(self):
        for seed in range(4):
            result = generate(['о свежих пельменях', 'Я опять хочу спать',
                               'Я давно ушел отдыхать'], seed)
            self.assertIsNotNone(result)
            self.assertTrue(result.text.startswith('Свежие пельмени'), result.text)
            self.assertRegex(result.text, r'хотят|ушли')
            self.assertNotRegex(result.text, r'\b(?:хочет|ушел|ушёл|ушла)\b')

    def test_adjective_homonym_is_a_modifier_of_the_following_object(self):
        for seed in range(12):
            result = generate(['с синей клавиатурой', 'Я снова хочу спать',
                               'Я давно забыл поесть'], seed)
            self.assertIsNotNone(result)
            self.assertTrue(result.text.startswith('Синяя клавиатура'), result.text)

    def test_short_messages_and_a_standalone_noun_can_supply_material(self):
        result = generate(['Холодильник', 'Я опять хочу спать', 'Я давно ушел отдыхать'])
        self.assertIsNotNone(result)
        self.assertTrue(result.text.startswith('Холодильник'), result.text)
        short = generate(['Поставил чайник', 'Купил пельмени', 'Забыл будильник'])
        self.assertIsNotNone(short)

    def test_people_names_and_human_roles_do_not_supply_object_characters(self):
        self.assertIsNone(generate(['Поговорил с Иваном', 'Поблагодарил старого врача',
                                    'Позвал новую участницу']))

    def test_commands_and_unsupported_clauses_do_not_seed_the_dictionary(self):
        self.assertIsNone(generate(['Старая клавиатура', '/Купил чайник',
                                    '/Открыл холодильник', 'Я прочитал 12 книг',
                                    'Я открыл private dashboard', 'https://example.com/чайник']))

    def test_verb_and_predicative_homonyms_do_not_become_subjects(self):
        sources = ['Мы были очень рады отдыху', 'Я пила кофе', 'Я опять хочу спать']
        results = [generate(sources, seed) for seed in range(12)]
        self.assertTrue(any(results))
        for result in results:
            if result:
                self.assertNotRegex(result.text, r'^(?:Рада|Пила)\b')

    def test_supported_degree_adverbs_preserve_full_and_negative_predicates(self):
        for speech, expected in [
            ('Я был совершенно счастливым', 'совершенно счастливой'),
            ('Я был совершенно не готов работать', 'совершенно не готова'),
        ]:
            with self.subTest(speech=speech):
                result = generate(['Старая клавиатура', speech])
                self.assertIsNotNone(result)
                self.assertIn(expected, result.text)

    def test_unsupported_impersonal_infinitive_does_not_get_a_nominative_subject(self):
        for speech in ('Пришлось рано уйти', 'Было страшно звонить',
                       'Было страшно снова звонить', 'Было страшно даже снова звонить'):
            with self.subTest(speech=speech):
                self.assertIsNone(generate(['Старая клавиатура', speech]))

    def test_explicit_plural_modifier_disambiguates_object_number(self):
        for speech, subject in [('купил новые окна', 'новые окна'),
                                ('купил новые книги', 'новые книги'),
                                ('заменил старые колеса', 'старые колеса'),
                                ('купил старые дома', 'старые дома')]:
            with self.subTest(speech=speech):
                result = generate([speech, 'Я опять хочу спать', 'Я давно ушел отдыхать'], 0)
                self.assertIsNotNone(result)
                self.assertTrue(normalize_source_text(result.text).startswith(subject), result.text)
                self.assertRegex(result.text, r'хотят|ушли')

    def test_human_role_homonyms_do_not_borrow_an_object_reading(self):
        for speech in ('Позвал нового гения', 'Поговорил с новым главой',
                       'Поговорил с новой главой', 'Писал главе',
                       'Писал про нового главу', 'Писал для новой главы',
                       'Писал про совершенно новую главу'):
            with self.subTest(speech=speech):
                self.assertIsNone(generate([speech, 'Я опять хочу спать', 'Я давно ушел отдыхать'], 0))

    def test_writing_object_context_still_learns_a_chapter(self):
        for speech, prefix in [('дописываю главу', 'глава'),
                               ('дописываю последнюю главу', 'последняя глава')]:
            with self.subTest(speech=speech):
                result = generate([speech, 'Я опять хочу спать', 'Я давно ушел отдыхать'], 0)
                self.assertIsNotNone(result)
                self.assertTrue(normalize_source_text(result.text).startswith(prefix), result.text)

    def test_short_predicative_aliases_do_not_need_an_explicit_human_subject(self):
        for speech in ('Очень рады', 'Рады', 'Снова рады'):
            with self.subTest(speech=speech):
                self.assertIsNone(generate([speech, 'Я опять хочу спать', 'Я давно ушел отдыхать'], 0))

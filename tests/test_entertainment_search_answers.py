"""Contract tests use a local HTTP server; no real key or Tavily credits."""
import asyncio
import unittest
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestServer

from entertainment.search_answers import (
    SearchFailure, TavilySearchClient, extract_search_question, checked_search_answer,
)
from entertainment.moderation_safety import is_safe_entertainment_reply


ANSWER = 'Небо кажется голубым из-за рассеяния солнечного света в атмосфере.'


def payload(answer=ANSWER):
    return {'query': 'Почему небо голубое?', 'answer': answer,
            'results': [{'url': 'https://science.example/sky', 'title': 'Рассеяние света',
                         'content': 'Молекулы воздуха рассеивают короткие волны сильнее длинных.',
                         'score': 0.95}], 'response_time': 0.1,
            'usage': {'credits': 1}, 'request_id': 'synthetic-request'}


class SearchQuestionTests(unittest.TestCase):
    def test_explicit_imperative_search_requests_are_recognized(self):
        for request in ('скажи погоду на завтра спб', 'расскажи о фотосинтезе',
                        'объясни почему небо голубое', 'найди информацию о Марсе'):
            for separator in (', ', ' '):
                with self.subTest(request=request, separator=separator):
                    self.assertEqual(extract_search_question('Фосген' + separator + request), request)

    def test_any_text_after_name_is_a_search_request(self):
        for name in ('фосген', 'Фосген', 'ФОСГЕН', 'фОсГеН'):
            for request in ('погода на завтра спб', 'привет', 'скажи', 'скажите погоду', '2 + 2'):
                for separator in (' ', ', ', ': ', '. ', ' — '):
                    with self.subTest(name=name, request=request, separator=separator):
                        self.assertEqual(extract_search_question(name + separator + request), request)

    def test_address_requires_name_boundary_and_nonempty_subject(self):
        for text in ('скажи погоду на завтра спб', 'Фосген', 'Фосген,   ',
                     'Фосген!!!', 'Фосгенчик погода', 'Фосген2026 погода',
                     'Я сказал: Фосген, найди информацию', '/Фосген, объясни фотосинтез'):
            with self.subTest(text=text):
                self.assertIsNone(extract_search_question(text))

    def test_factual_second_person_is_allowed_but_entertainment_remains_strict(self):
        text = 'Благодаря фотосинтезу вы получаете кислород.'
        self.assertEqual(checked_search_answer(text), text)
        self.assertFalse(is_safe_entertainment_reply(text))

    def test_search_uses_its_own_700_character_limit(self):
        text = ('Растения используют солнечный свет. ' * 21)[:700]
        self.assertEqual(len(text), 700)
        self.assertEqual(checked_search_answer(text), text)
        self.assertFalse(is_safe_entertainment_reply(text))
        with self.assertRaisesRegex(SearchFailure, 'answer_too_long'):
            checked_search_answer(text + 'а')

    def test_search_still_rejects_abuse_profanity_and_targeted_mentions(self):
        for text in ('Ты идиот.', 'Это позор нашего чата.', 'Ты никому не нужен.',
                     'Блядь, это фотосинтез.', 'Растения выделяют кислород для @someone.'):
            with self.subTest(text=text), self.assertRaisesRegex(SearchFailure, 'answer_safety'):
                checked_search_answer(text)

    def test_only_explicit_addressed_questions_are_extracted(self):
        for incoming in ('Фосген, почему небо голубое?', 'фосген а почему небо голубое?',
                         'ФОСГЕН: почему небо голубое?'):
            with self.subTest(incoming=incoming):
                self.assertEqual(extract_search_question(incoming), 'почему небо голубое?')
        for incoming in (None, 'Почему небо голубое?', 'Я спросил Фосгена почему',
                         '/Фосген почему небо голубое?', 'Фосген'):
            with self.subTest(incoming=incoming):
                self.assertIsNone(extract_search_question(incoming))

    def test_common_interrogative_genders_and_cases_remain_search_questions(self):
        for question in ('какая столица Франции?', 'какого цвета небо?',
                         'какому жанру относится роман?', 'какие бывают звезды?',
                         'какую планету называют красной?', 'каким бывает свет?'):
            with self.subTest(question=question):
                self.assertEqual(extract_search_question('Фосген, ' + question), question)


class TavilyClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []
        self.response = payload()
        self.status = 200
        self.raw = None
        self.delay = 0
        self.redirect = None

        async def respond(request):
            self.requests.append((dict(request.headers), await request.json()))
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.redirect:
                raise web.HTTPFound(self.redirect)
            if self.raw is not None:
                return web.Response(body=self.raw)
            return web.json_response(self.response, status=self.status)

        application = web.Application()
        application.router.add_post('/search', respond)
        self.server = TestServer(application)
        await self.server.start_server()
        self.endpoint = patch('entertainment.search_answers.SEARCH_ENDPOINT',
                              str(self.server.make_url('/search')))
        self.endpoint.start()
        self.client = TavilySearchClient('synthetic-secret', timeout=0.3)

    async def asyncTearDown(self):
        self.endpoint.stop()
        await self.server.close()

    async def test_one_basic_search_returns_short_plain_russian_answer(self):
        self.assertEqual(await self.client.answer('Почему небо голубое?'), ANSWER)
        self.assertEqual(len(self.requests), 1)
        headers, body = self.requests[0]
        self.assertEqual(headers['Authorization'], 'Bearer synthetic-secret')
        self.assertEqual(body, {
            'query': 'Почему небо голубое?', 'search_depth': 'basic',
            'auto_parameters': False, 'include_answer': 'basic', 'max_results': 3,
            'language': 'ru', 'filter_by_language': True,
            'safe_search': True, 'include_raw_content': False,
        })
        self.assertNotIn('synthetic-secret', repr(self.client))

    async def test_rejects_ungrounded_unsafe_non_russian_or_oversized_answers(self):
        invalid = [None, [], payload(''), payload('The sky is blue because of scattering.'),
                   payload('Слово ' * 150), payload('Это позор нашего чата.'),
                   payload('Ответ: https://untrusted.example'), payload('<b>Это ответ.</b>')]
        for result in ([], [{'url': 'file:///etc/passwd', 'content': 'текст', 'score': 1}],
                       [{'url': 'https://example.test', 'content': 'не по теме', 'score': .05}]):
            data = payload()
            data['results'] = result
            invalid.append(data)
        for value in invalid:
            with self.subTest(value=value):
                self.response = value
                with self.assertRaises(SearchFailure):
                    await self.client.answer('Почему небо голубое?')

    async def test_http_errors_do_not_retry_or_expose_response_secrets(self):
        for status in (401, 429, 432, 433, 500):
            self.status = status
            self.response = {'detail': 'synthetic-secret private query'}
            before = len(self.requests)
            with self.subTest(status=status), self.assertRaises(SearchFailure) as caught:
                await self.client.answer('Почему небо голубое?')
            self.assertEqual(len(self.requests), before + 1)
            self.assertNotIn('synthetic-secret', str(caught.exception))
            self.assertNotIn('private query', str(caught.exception))

    async def test_redirect_does_not_forward_credentials(self):
        self.redirect = str(self.server.make_url('/search?redirected=yes'))
        with self.assertRaises(SearchFailure):
            await self.client.answer('Почему небо голубое?')
        self.assertEqual(len(self.requests), 1)

    async def test_invalid_json_and_bounded_response_fail_closed(self):
        for raw in (b'not json', b' ' * (128 * 1024 + 1)):
            self.raw = raw
            with self.subTest(size=len(raw)), self.assertRaises(SearchFailure):
                await self.client.answer('Почему небо голубое?')

    async def test_timeout_is_a_failure_without_a_second_search(self):
        self.delay = .05
        client = TavilySearchClient('synthetic-secret', timeout=.01)
        with self.assertRaises(SearchFailure):
            await client.answer('Почему небо голубое?')
        self.assertEqual(len(self.requests), 1)

    async def test_invalid_input_does_not_spend_credits(self):
        for query in ('', 'я' * 501):
            with self.subTest(query_length=len(query)), self.assertRaises(SearchFailure):
                await self.client.answer(query)
        self.assertEqual(self.requests, [])

    async def test_high_scored_but_unrelated_answer_is_not_grounded(self):
        self.response = payload('Столица Франции — Берлин.')
        with self.assertRaises(SearchFailure):
            await self.client.answer('Почему небо голубое?')

    async def test_supported_answer_to_a_different_question_is_rejected(self):
        with self.assertRaises(SearchFailure):
            await self.client.answer('Какая столица Франции?')

    async def test_inflected_supported_words_and_named_subjects_remain_usable(self):
        self.response = payload('Париж является столицей Франции.')
        self.response['results'][0].update(
            title='Франция', content='Столица Франции — Париж, крупнейший город страны.')
        self.assertEqual(await self.client.answer('Какая столица Франции?'),
                         'Париж является столицей Франции.')

    async def test_failure_categories_preserve_safe_http_status(self):
        for status, reason in ((401, 'http_auth'), (403, 'http_access'),
                               (400, 'http_request'), (422, 'http_request'),
                               (429, 'http_rate_limit'), (432, 'http_quota'),
                               (433, 'http_quota'), (434, 'http_capacity'),
                               (503, 'http_provider')):
            self.status = status
            self.response = {'detail': 'synthetic-secret private question'}
            with self.subTest(status=status), self.assertRaises(SearchFailure) as caught:
                await self.client.answer('Почему небо голубое?')
            self.assertEqual(str(caught.exception), reason)
            self.assertEqual(caught.exception.http_status, status)

    async def test_http200_filter_rejection_retains_status_and_specific_reason(self):
        for answer, reason in (('', 'missing_answer'), ('Слово ' * 150, 'answer_too_long'),
                               ('Ответ: https://example.test', 'answer_links'),
                               ('The sky is blue.', 'answer_language')):
            self.response = payload(answer)
            with self.subTest(reason=reason), self.assertRaises(SearchFailure) as caught:
                await self.client.answer('Почему небо голубое?')
            self.assertEqual(str(caught.exception), reason)
            self.assertEqual(caught.exception.http_status, 200)

    async def test_timeout_has_a_separate_diagnostic_reason(self):
        self.delay = .05
        with self.assertRaises(SearchFailure) as caught:
            await TavilySearchClient('synthetic-secret', timeout=.01).answer('Почему небо голубое?')
        self.assertEqual(str(caught.exception), 'timeout')
        self.assertIsNone(caught.exception.http_status)


if __name__ == '__main__':
    unittest.main()

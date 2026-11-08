"""Contract tests use a local HTTP server; no real key or Tavily credits."""
import asyncio
import unittest
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestServer

from entertainment.search_answers import (
    SearchFailure, TavilySearchClient, extract_search_question,
)


ANSWER = 'Небо кажется голубым из-за рассеяния солнечного света в атмосфере.'


def payload(answer=ANSWER):
    return {'query': 'Почему небо голубое?', 'answer': answer,
            'results': [{'url': 'https://science.example/sky', 'title': 'Рассеяние света',
                         'content': 'Молекулы воздуха рассеивают короткие волны сильнее длинных.',
                         'score': 0.95}], 'response_time': 0.1,
            'usage': {'credits': 1}, 'request_id': 'synthetic-request'}


class SearchQuestionTests(unittest.TestCase):
    def test_only_explicit_addressed_questions_are_extracted(self):
        for incoming in ('Фосген, почему небо голубое?', 'фосген а почему небо голубое?',
                         'ФОСГЕН: почему небо голубое?'):
            with self.subTest(incoming=incoming):
                self.assertEqual(extract_search_question(incoming), 'почему небо голубое?')
        for incoming in (None, 'Почему небо голубое?', 'Я спросил Фосгена почему',
                         '/Фосген почему небо голубое?', 'Фосген привет', 'Фосген'):
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
            'language': 'ru', 'safe_search': True, 'include_raw_content': False,
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


if __name__ == '__main__':
    unittest.main()

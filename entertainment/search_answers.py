"""One bounded basic Tavily search, with no retries or paid-service fallback."""
from __future__ import annotations

import asyncio
import json
import re
from urllib.parse import urlsplit

import aiohttp

from .moderation_safety import is_safe_search_reply
from .language import analyze_token

SEARCH_ENDPOINT = 'https://api.tavily.com/search'
_MAX_RESPONSE_BYTES = 128 * 1024
_ADDRESS = re.compile(
    r'^\s*фосген(?:\s*[,!:—-]\s*|\s+)(?:а\s+)?'
    r'((?:что|почему|как|кто|где|когда|зачем|сколько|'
    r'как(?:ой|ая|ое|ие|ого|ому|ую|их|им|ими|ом)|чем)\b.+)\s*$',
    re.IGNORECASE | re.DOTALL,
)
_LINK = re.compile(r'https?://|www\.|t\.me/|<[^>]*>|\[[^\]]+\]\([^)]*\)', re.I)
_TERMS = re.compile(r'[A-Za-zА-Яа-яЁё]+(?:-[A-Za-zА-Яа-яЁё]+)*|\d+(?:[.,]\d+)?')
_FILLER = frozenset({
    'что', 'почему', 'как', 'кто', 'где', 'когда', 'зачем', 'сколько', 'какой',
    'чем', 'такой', 'это', 'этот', 'также', 'мочь', 'означать', 'значить',
    'называть', 'быть', 'являться', 'иметь', 'свой', 'весь', 'один', 'самый',
    'the', 'and', 'for', 'with', 'from', 'that', 'this',
})


class SearchFailure(Exception):
    """A safe operational category, never provider bodies or user text."""

    def __init__(self, reason: str, *, http_status: int | None = None) -> None:
        super().__init__(reason)
        self.http_status = http_status


def extract_search_question(text: str | None) -> str | None:
    if not isinstance(text, str):
        return None
    match = _ADDRESS.match(text)
    return ' '.join(match.group(1).split()) if match else None


def checked_search_answer(value: object) -> str:
    if not isinstance(value, str):
        raise SearchFailure('missing_answer')
    text = ' '.join(value.replace('**', '').replace('__', '').replace('`', '').split())
    if not text:
        raise SearchFailure('missing_answer')
    if len(text) > 700:
        raise SearchFailure('answer_too_long')
    if _LINK.search(text):
        raise SearchFailure('answer_links')
    cyrillic = len(re.findall('[А-Яа-яЁё]', text))
    letters = sum(c.isalpha() for c in text)
    if cyrillic < 4 or cyrillic < letters / 2:
        raise SearchFailure('answer_language')
    if not is_safe_search_reply(text):
        raise SearchFailure('answer_safety')
    return text


def _content_terms(text: str) -> set[str]:
    terms = set()
    for word in _TERMS.findall(text)[:256]:
        parsed = analyze_token(word)
        lemma = parsed.lemma.replace('ё', 'е')
        if (lemma not in _FILLER and parsed.pos not in {'NPRO', 'PREP', 'CONJ', 'PRCL', 'INTJ'}
                and (len(lemma) >= 3 or word[0].isdigit())):
            terms.add(lemma)
    return terms


def _grounded_answer(data: object, question: str) -> str:
    if not isinstance(data, dict) or not isinstance(data.get('results'), list):
        raise SearchFailure('missing_results')
    evidence = []
    for result in data['results'][:3]:
        if not isinstance(result, dict):
            continue
        url, content, score = result.get('url'), result.get('content'), result.get('score')
        if (isinstance(url, str) and urlsplit(url).scheme in {'http', 'https'}
                and urlsplit(url).hostname and isinstance(content, str) and content.strip()
                and type(score) in {int, float} and .35 <= score <= 1):
            title = result.get('title')
            evidence.append((title[:200] if isinstance(title, str) else '') + ' ' + content[:1500])
    if not evidence:
        raise SearchFailure('missing_results')
    answer = checked_search_answer(data.get('answer'))
    answer_terms = _content_terms(answer)
    evidence_terms = set().union(*(_content_terms(source) for source in evidence))
    shared = answer_terms & evidence_terms
    if (not answer_terms or len(shared) < min(2, len(answer_terms))
            or len(shared) / len(answer_terms) < .25
            or not (_content_terms(question) & (answer_terms | evidence_terms))):
        raise SearchFailure('unsupported_answer')
    return answer


class TavilySearchClient:
    def __init__(self, api_key: str, *, timeout: float = 5) -> None:
        self._api_key = api_key.strip()
        self._timeout = min(5, max(.001, timeout))

    async def answer(self, question: str) -> str:
        if not self._api_key or not question.strip() or len(question) > 500:
            raise SearchFailure('invalid_request')
        body = {
            'query': question, 'search_depth': 'basic', 'auto_parameters': False,
            'include_answer': 'basic', 'max_results': 3, 'language': 'ru',
            'safe_search': True, 'include_raw_content': False,
        }
        status = None
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self._timeout), trust_env=False,
            ) as session:
                async with session.post(
                    SEARCH_ENDPOINT, json=body,
                    headers={'Authorization': f'Bearer {self._api_key}'},
                    allow_redirects=False,
                ) as response:
                    status = response.status
                    if status != 200:
                        reason = {
                            400: 'http_request', 401: 'http_auth', 403: 'http_access',
                            422: 'http_request', 429: 'http_rate_limit',
                            432: 'http_quota', 433: 'http_quota', 434: 'http_capacity',
                        }.get(status, 'http_provider' if status >= 500 else 'http_error')
                        raise SearchFailure(reason, http_status=status)
                    chunks, size = [], 0
                    async for chunk in response.content.iter_chunked(8192):
                        size += len(chunk)
                        if size > _MAX_RESPONSE_BYTES:
                            raise SearchFailure('response_size')
                        chunks.append(chunk)
                    data = json.loads(b''.join(chunks))
            return _grounded_answer(data, question)
        except SearchFailure as exc:
            exc.http_status = status
            raise
        except asyncio.TimeoutError:
            raise SearchFailure('timeout', http_status=status) from None
        except aiohttp.ClientError:
            raise SearchFailure('transport', http_status=status) from None
        except (ValueError, TypeError):
            raise SearchFailure('response_format', http_status=status) from None

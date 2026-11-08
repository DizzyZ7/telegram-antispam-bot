# Free Search Answers Implementation Plan

> For agentic workers: use superpowers:executing-plans; test RED→GREEN, then one independent whole-branch review.

Goal: short factual answers to explicit Fosgen questions, using free Tavily and exact fallback.
Architecture: a bounded aiohttp client and separate search handler, with atomic request claims in existing storage. Learning middleware bypasses recognized search requests; moderation keeps handler priority.
Tech Stack: Python 3.12, aiogram, aiohttp, existing SQLite/PostgreSQL.
Spec: docs/superpowers/specs/2026-11-08-free-search-answers-design.md

## Global Constraints

Only TAVILY_API_KEY enables the feature. Basic search, no automatic parameters/paid fallbacks/retries. 500 input characters, 700 output characters, 5-second HTTP and 7-second handler deadline, 128 KiB response cap. Failure text exactly «Пу пу пу». Preserve scope/moderation and local entertainment.

## Review Focus

- Transport failures after sending a reply must not produce a second message.
- A syntactically valid but ungrounded/English/oversized answer must fail closed.
- Incoming text filtered by moderation must never cause an external request.
- Duplicate Telegram deliveries across restart must not spend a second credit.
- Missing key must preserve all previous local bot behavior and diagnostics.

### Task 1: Bounded search client

Files: entertainment/search_answers.py; tests/test_entertainment_search_answers.py.
Interfaces: extract_search_question(text) -> str|None; TavilySearchClient(api_key, timeout=5).answer(question) -> str, raises SearchFailure(reason).
- [x] Write parser/output contract and local aiohttp-server tests: basic one-call payload, short Russian answer, no links, missing/irrelevant results, malformed/oversized data, 429/432/500, timeout, redirect without secret forwarding.
- [x] Run tests and observe failures because the feature is missing.
- [x] Implement bounded transport and conservative answer validation; run client tests; commit.

### Task 2: Scoped one-message runtime

Files: entertainment/search_service.py; dialogue_service.py; router.py; models.py; storage/base.py, sqlite.py, postgres.py; tests/test_entertainment_search_service.py; tests/test_entertainment_storage_postgres.py.
Interfaces: storage.claim_search_request(chat_id, topic_id, message_id, created_at)->bool; SearchAnswerService(storage,is_allowed_scope,provider,now_fn).matches(message)->bool and handle(message)->None.
- [x] Write failing exported-service, real SQLite/restart/concurrent claim, separate-chat, settings/quiet-hours, safety, cooldown, duplicate, failure/send-error and real handler-boundary tests. Add PostgreSQL parity test for CI.
- [x] Observe RED. Add the scoped search service, atomic storage claim/index, handler registration and learning-queue bypass. Use existing moderation order and no new background workers.
- [x] Run client/runtime/PostgreSQL-available tests and existing dialogue/learning/moderation regressions; commit.

### Task 3: Verify and deliver

Files: .env.example; docs/fosgen-search-answers.md; docs/entertainment-generation-v3.md.
- [x] Document registration, free-account settings, TAVILY_API_KEY activation/disable, response cap, fallback, migration and live quality check still pending.
- [x] Run full Python suite, 65 Node client tests, compileall, diff check. Request one independent review, fix Important findings RED→GREEN plus full suite.
- [ ] Publish PR, inspect CI and integrate verified head in main under the established authorization; distinguish merged code from Bothost activation/live quality.

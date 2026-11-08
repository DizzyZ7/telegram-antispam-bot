# Search Failure Diagnostics Implementation Plan

> For agentic workers: use superpowers:executing-plans; native implementation with one whole-branch review.

Goal: make the actual production rejection visible without asking for a secret or relying on INFO logs.
Architecture: safe typed failure metadata, bounded per-scope last-result cache, stdout event and scoped admin command. No change to answer acceptance or HTTP budgets.
Tech Stack: existing Python/aiogram/aiohttp.
Spec: docs/superpowers/specs/2026-11-08-search-diagnostics-design.md

## Global Constraints

700 output characters, 500 input, 5-second search and 2-second claim unchanged. No real Tavily calls, paid endpoints, raw payload logs or new dependencies/schema. Ordinary fallback stays exactly «Пу пу пу».

## Review Focus

- An HTTP 200 rejected by language/length checks must retain status and a distinct safe reason.
- Arbitrary exception text and secret-valued metadata never reach stdout or admin status.
- Unauthorized and blocked-topic status requests cannot inspect results or call Tavily.
- Interleaved topics and Absent/zero topic aliases cannot expose another topic's state.
- Delivery failure, duplicate update and claim outage must preserve one-message semantics and useful diagnostics.

### Task 1: Reproduce and expose failures

Files: entertainment/search_answers.py, search_service.py; tests/test_entertainment_search_answers.py; tests/test_entertainment_search_service.py.
Interfaces: SearchFailure(reason, http_status=None) retains str compatibility; SearchAnswerService.status_text(chat_id,topic_id)->str returns sanitized process-local status.
- [x] Add failing HTTP-category, HTTP200-filter, timeout, stdout-under-disabled-logging, last-result, isolation/privacy, eviction and duplicate/delivery tests.
- [x] Observe RED, implement safe categories and bounded diagnostics; run search regressions and commit.

### Task 2: Admin route and release

Files: entertainment/dialogue_service.py, router.py; tests/test_entertainment_search_service.py; docs/fosgen-search-answers.md.
Interface: exported EntertainmentService.show_search_status(message)->None applies scope before admin check, then returns current-scope status without network/storage reads for the diagnostic itself.
- [x] Add failing admin/deny/no-API/real-handler-route tests, observe RED and implement command.
- [x] Document update, one new question, then /fun_search_status in the same topic; restart resets previous diagnostics. Keep actual root cause pending evidence.
- [ ] Run full Python, Node65, compilation/diff checks, one independent review and any Important fixes RED→GREEN. Publish, inspect CI and merge verified code under established authorization.

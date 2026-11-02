# IKF Birthday Implementation Plan

> For agentic workers: use superpowers:executing-plans; one final independent review.

Goal: deliver varied warm birthday greetings once per chat per Moscow day,
including the approved storyteller plan by context or its 12:30 fallback.
Architecture: authored paragraph pools, a bounded one-off JSON plan, a shared
coordinator for message/supervisor paths, and daily claims in both backends.
Tech stack: existing Python/aiogram, SQLite/PostgreSQL, unittest.
Spec: `docs/superpowers/specs/2026-11-02-ikf-birthday-design.md`.
Constraints: no external AI, credentials, new dependencies, inferred profile
data, annual recurrence or changes outside allowed chats.

Review focus: wrong-recipient congratulations; source/processing Moscow date
boundaries; daily quota across restart/topics/replicas; message/timer races;
uncertain Telegram result; first birthday phrase and repeated trigger spam;
production entry points and the legacy hourly fallback.

## Task 1: Addressed and generic birthday delivery under one daily quota

Files: create `entertainment/birthdays.py`, `entertainment/birthday_plans.json`,
`entertainment/birthday_texts.py`,
`tests/test_entertainment_birthdays.py`, `docs/ikf-birthday-greetings.md`;
modify `entertainment/service.py`, the production `dialogue_service.py`, both core storage backends, and CI.
Interfaces: `BirthdayPlan`, `BirthdayCoordinator.tick(now)` and
`observe(message, now)`; backend `claim_birthday_delivery`,
`finish_birthday_delivery`, `release_birthday_delivery`.

- [ ] Write tests for time/context matching, wrong recipients, shared daily
  quota, first phrase and repeated triggers, cross-topic/restart deduplication, Moscow
  midnight, authored variety, concurrent paths and uncertain delivery.
- [ ] Run tests red; implement minimal coordinator and durable backends.
- [ ] Verify service wiring, full Python and JavaScript suites, PostgreSQL CI.
- [ ] Independent review, fix material findings with regressions, publish PR
  and merge after green CI under existing user authorization.

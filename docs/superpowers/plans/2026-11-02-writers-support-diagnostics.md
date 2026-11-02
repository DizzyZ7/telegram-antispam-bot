# Writers Support Diagnostics Implementation Plan

> For agentic workers: use superpowers:executing-plans for inline execution and one final independent review.

Goal: make a support ticket explain the operation and failure and link it to server logs.

Architecture: extend the existing browser HTTP helpers and support endpoint with
a bounded, allowlisted envelope; add server-generated request references.
Tech stack: JavaScript, aiohttp, Python unittest and Node's test runner.
Spec: `docs/superpowers/specs/2026-11-02-writers-support-diagnostics-design.md`.

Global constraints: no new dependencies/schema; no form values or raw logs in
diagnostics; preserve save/submit reconciliation and authenticated access.

Review focus: recovery reads must preserve the original failure; expired initData
must still allow support with a valid session; malformed diagnostic fields must
not leak arbitrary text; XHR failures need the same coverage as fetch; legacy
clients without a diagnostic envelope must still be accepted.

## Task 1: Structured diagnostics end to end

Files: modify `writers_submission/static/app.js`, `writers_submission/web.py`,
`writers_submission/static/index.html`, `.github/workflows/ci.yml`; create
`writers_submission/support.py` and `tests/test_writers_support_client.cjs`;
extend `tests/test_writers_submission_web.py`.

Interfaces: support POST retains `init_data`, `platform`, `category` and adds
`diagnostics`; errors retain existing status/code and gain safe reference data.
The server returns `X-Request-ID` for every writers API request and logs it.

- [ ] Write regression tests for failed save/submit/upload, recovery reads,
  client platform/version and safe snapshot contents, backend sanitization,
  real message newlines, authenticated identity and log correlation.
- [ ] Run the new tests and observe missing diagnostic behavior.
- [ ] Implement bounded browser history and failure metadata, report formatting,
  normalized client environment and authenticated support fallback.
- [ ] Run Node client suites and Python unittest discovery; inspect failures.
- [ ] Review the whole change independently, address material findings, commit,
  open a PR, and merge if all required CI checks pass under existing authorization.

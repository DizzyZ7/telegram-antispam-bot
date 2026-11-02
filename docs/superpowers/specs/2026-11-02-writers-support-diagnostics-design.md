# Mini App support diagnostics

The first support ticket contains only an ID, `unknown` and `save_failed`.
The browser currently infers the category from `bootstrapped`, never sends the
actual error, and discards the server's error reference. Telegram newlines are
also escaped twice. This change makes the existing support button useful for
diagnosing failures without sending work content.

Use the existing consent and support endpoint. Capture a bounded, memory-only
history of eight HTTP operations, keeping the last failed operation separately
so recovery reads cannot erase it. Send operation, method, status, stable error
and validation codes, UTC time, duration and a server-generated reference.
Include build/Telegram versions, online state, draft UUID/version/status,
unsaved-state flags and attachment counts. Never include form values, links,
filenames, file contents, raw exceptions, raw user-agent, cookies or tokens.

The backend accepts only known enums, bounded integers, numeric versions and
well-formed references/UUIDs; it derives coarse OS/browser information from the
request without retaining the raw header. Client diagnostics are explicitly
unverified. Actor identity comes from signed Telegram data or a valid signed
session cookie. Sending a report must not depend on the writers database.
Each API response and associated log record share a generated `X-Request-ID`.
Unexpected error JSON uses that same reference. Reports have real newlines and
remain under Telegram's 4096-character limit.

Alternatives: reporting only a friendlier `save_failed` still loses the cause;
sending raw browser logs or full form snapshots sends unnecessary sensitive
content. A small structured diagnostic envelope fixes the actual information
loss using the current stack, without dependencies or a new storage schema.

Existing save reconciliation, idempotency, moderation and entertainment behavior
must remain intact. An old ticket cannot be reconstructed retroactively.

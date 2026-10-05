# Entertainment — moderation safety boundary

Entertainment must never turn content removed by writers-chat moderation into bot vocabulary.

## Input safety

Before Culture Memory stores a human text or caption, the production Entertainment service checks it against the writers-chat moderation policy. Prohibited language is not added to `ent_memory_events` or `entertainment_messages` and cannot trigger generation.

The check is applied after the existing hard `(chat_id, topic_id)` denylist, so blocked topics remain invisible to Entertainment before message text is inspected.

## Historical safety

Existing Culture Memory rows are filtered again when a generation snapshot is built. Unsafe historical text/captions are excluded from recent and sampled historical context. This protects installations that already contain older dirty rows without requiring a destructive migration.

The Entertainment matcher mirrors the runtime moderation sanitation and removes mixed-script collisions created from pure-Latin transliteration rules, so normal Russian words do not become false positives when the package is imported outside `main.py`.

## Output safety

Generated text is checked before delivery. An unsafe candidate is discarded and generation may retry with another candidate; if no safe output is available, Entertainment returns no text. Generation observability still records one logical attempt rather than one metric entry per safety retry.

## Moderation deletion purge

When the writers-chat moderation handler successfully deletes a prohibited Telegram message, it calls the configured deletion hook with `(chat_id, message_id)`. Production wires that hook to `EntertainmentStorage.delete_message_memory`.

The SQLite and PostgreSQL implementations delete the target message atomically from both projections:

- `ent_memory_events` (Culture Memory);
- `entertainment_messages` (legacy/activity text projection).

Deletion is scoped by both `chat_id` and `message_id`; an equal Telegram message ID in another chat is preserved. If Telegram deletion itself fails, no database purge is claimed. If the database purge fails after Telegram deletion, moderation remains fail-safe: the warning flow continues and the purge error is logged.

No schema migration or new dependency is required because both existing projections already persist `message_id`.

## Telegram limitation

The normal Bot API does not provide a general update for every arbitrary historical message manually deleted by a user or administrator. Therefore this guarantee applies to messages deleted through the bot's moderation flow. Historical unsafe rows are still prevented from reaching generation by the read/output safety gates above.

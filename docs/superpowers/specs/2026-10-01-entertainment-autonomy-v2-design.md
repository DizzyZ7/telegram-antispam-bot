# Entertainment Autonomy v2 — design

Date: 2026-10-01
Repository: `DizzyZ7/telegram-antispam-bot`
Primary deployment: Bothost
Initial enabled chat: `-1002619489118`

## 1. Goal

Turn the existing entertainment layer into a distinct, autonomous chat personality that belongs to this bot rather than copying another product's terminology or UX.

The bot should feel alive inside selected chats: it observes conversation, learns local patterns, remembers media and recurring jokes, occasionally contributes something relevant, and creates new lightweight entertainment such as remixed text, quotes, polls, memes, image captions and callbacks to earlier chat moments.

Success means:

- autonomous behavior is useful and funny rather than noisy;
- no wording, settings model, labels or interaction patterns are copied from Sglypa;
- entertainment remains isolated to explicitly enabled chats;
- forum topics do not leak context into unrelated topics;
- moderation, Lexicon and existing bot features continue to work independently;
- the bot survives Bothost restarts and deploys cleanly;
- local functionality works without any external AI provider;
- richer AI generation can be added later behind provider adapters without becoming a hard dependency.

## 2. Product identity

The entertainment system is not a generic command pack. It is a "digital resident" of the chat with its own local behavior model.

User-facing terminology should be ours. Avoid copied concepts such as a numeric "laziness" control. Instead expose understandable behavior modes and operational controls.

Initial public controls:

- `Спокойный` — rare autonomous participation;
- `Живой` — balanced default;
- `Активный` — more frequent entertainment, still rate-limited;
- quiet hours;
- per-feature toggles for text, quotes, memes, polls, media callbacks and reactions;
- memory/status page;
- emergency disable.

Internally these presets map to thresholds and budgets, but raw probabilities should not be the primary UX.

## 3. Scope

### 3.1 In scope for v2

- refactor the current monolithic `entertainment.py` into a package;
- autonomous decision engine based on recent chat activity and cooldown/budget state;
- per-chat and per-topic context isolation;
- PostgreSQL-first persistence with SQLite fallback;
- text memory and media metadata memory;
- Telegram `file_id` reuse for photos, stickers and animations;
- local text generation/remixing without external AI;
- quote and callback generation;
- local meme/card rendering with Pillow;
- lightweight polls/events;
- inline admin panel and presets;
- safe scheduling within the existing aiogram process;
- observability and action logs;
- migration path from the existing entertainment SQLite schema.

### 3.2 Deferred

- mandatory LLM dependency;
- mandatory image-generation API;
- Redis/Celery/RQ;
- separate worker service;
- vector database;
- speech cloning;
- autonomous web browsing;
- monetization.

These may be added later through adapters if there is a clear product need.

## 4. Deployment constraints

Bothost remains the primary target.

The bot should run as one Python/aiogram application process. No extra worker is required for the first production version. Autonomous tasks must be normal `asyncio` tasks owned by the application lifecycle and must stop cleanly during shutdown.

Persistent state must not depend only on process memory.

### 4.1 Database strategy

Preferred production database: PostgreSQL supplied by Bothost.

Fallback: SQLite for local development and emergency startup where PostgreSQL is intentionally not configured.

Configuration contract:

- `DATABASE_URL` present and PostgreSQL-compatible -> use PostgreSQL;
- `DATABASE_URL` absent -> use SQLite under `DATA_DIR`;
- entertainment code must use a storage interface so product logic does not depend on SQL dialect;
- PostgreSQL connection failure in production must fail clearly rather than silently create a second accidental SQLite universe unless an explicit fallback flag is enabled.

This avoids split-brain memory and lost chat history.

### 4.2 Media storage

Do not store Telegram media binaries in PostgreSQL.

Store:

- Telegram `file_id`;
- `file_unique_id` where available;
- chat/topic/message references;
- media type;
- dimensions/duration when useful;
- lightweight relevance metadata;
- timestamps and usage counters.

For locally rendered meme output, temporary files may be written under `DATA_DIR/cache` and cleaned by TTL. Generated media that should survive can usually be re-sent using Telegram `file_id` after the first upload and then persisted as metadata.

## 5. Proposed package structure

```text
entertainment/
  __init__.py
  config.py
  models.py
  router.py
  service.py
  engine.py
  context.py
  personality.py
  scheduler.py
  safety.py
  telemetry.py

  storage/
    base.py
    sqlite.py
    postgres.py
    migrations.py

  memory/
    text.py
    media.py
    topics.py

  generators/
    base.py
    conversation.py
    quotes.py
    polls.py
    events.py
    memes.py

  media/
    renderer.py
    templates.py

  providers/
    base.py
    local.py
    ai.py
```

`main.py` should only construct storage/service objects, register handlers and manage lifecycle.

## 6. Isolation model

Entertainment remains hard-scoped by an explicit chat allowlist.

Initial chat: `-1002619489118`.

Context key:

```text
(chat_id, topic_id)
```

For non-forum chats, `topic_id` is normalized to `0`.

Two memory levels exist:

1. topic-local memory — recent conversation, quotes, active jokes, media and autonomous timing;
2. chat-global culture — reusable sticker popularity, recurring expressions, general activity statistics.

Default generators use topic-local memory. Chat-global material is allowed only for features explicitly marked safe for cross-topic reuse, such as common stickers.

No entertainment data is mixed across separate `chat_id` values.

## 7. Autonomy engine

The engine replaces simple per-message random chance with stateful decision-making.

### 7.1 Inputs

For each `(chat_id, topic_id)` maintain rolling activity features:

- messages in last 1/5/15 minutes;
- distinct active users;
- text/media/reply ratios;
- elapsed time since last human message;
- elapsed time since last bot entertainment action;
- last autonomous action type;
- recent action counts;
- whether the conversation is accelerating or slowing;
- whether a recent message/media item is a strong candidate for a reply/meme;
- quiet-hours state;
- per-chat behavior preset;
- feature-specific cooldowns and daily/hourly budgets.

### 7.2 Conversation phases

The engine derives a coarse phase:

- `QUIET`
- `WARMING_UP`
- `ACTIVE`
- `PEAK`
- `COOLDOWN`

These are implementation details, not necessarily user-facing names.

Typical behavior:

- during `PEAK`, mostly observe and learn;
- during `ACTIVE`, only high-relevance replies/reactions may fire;
- during `COOLDOWN`, callbacks, quotes and memes become more eligible;
- during `QUIET`, occasional scheduled lightweight events may start if allowed.

### 7.3 Action candidates

The engine scores eligible candidates instead of blindly choosing a random action.

Initial action types:

- contextual reply;
- remixed phrase;
- quote callback;
- old-memory callback;
- sticker/animation callback;
- meme from recent image;
- caption card;
- poll;
- lightweight chat event;
- emoji reaction where supported and appropriate.

Each candidate supplies:

- relevance score;
- novelty score;
- cooldown readiness;
- estimated annoyance cost;
- required context/media;
- whether it is safe during active conversation;
- minimum memory requirements.

The engine selects at most one action per evaluation cycle.

## 8. Anti-spam policy

Autonomy must be conservative by design.

Default `Живой` preset target constraints:

- no more than 2 autonomous sends per 30 minutes per topic;
- at least one feature-specific cooldown after each autonomous send;
- no autonomous sequence of two messages without intervening human activity;
- no repeated action type back-to-back unless it is a direct reply;
- no resurrection of the same memory item too frequently;
- quiet-hours support;
- manual `/fun_off` must immediately suppress new autonomous actions while keeping other bot modules alive.

Presets adjust budgets and thresholds, not a single visible percentage.

## 9. Text memory and generation

### 9.1 What is learned

Eligible human messages only.

Exclude by default:

- bot messages;
- commands;
- obvious URLs-only content;
- service messages;
- very long dumps;
- moderation/system warnings;
- sensitive operational content where detectable;
- messages from ignored topics if configured.

### 9.2 No copy-paste behavior

The bot must not behave like a quote database when generating original text.

Generated text passes novelty checks:

- exact normalized message match -> reject;
- excessive n-gram overlap with one source -> reject;
- repeated output recently used by bot -> reject;
- too-close paraphrase where local heuristics detect it -> reject/retry.

Direct quote features are a separate explicit action type and should visibly look like a quote/callback, not pretend to be original generation.

### 9.3 Local generator

The current simple Markov approach can remain as a fallback, but v2 should improve it with phrase fragments and topic-local co-occurrence so output is less random and less likely to reproduce source sentences.

No external API is required for baseline text entertainment.

## 10. Media memory

Remember eligible:

- photos;
- stickers;
- animations/GIFs;
- optionally short videos as metadata only.

Media receives lightweight tags inferred from Telegram metadata and surrounding text/replies. Do not run heavyweight vision inference by default on Bothost.

Store popularity/usefulness signals:

- reply/reaction count proxy where available;
- number of times reused by bot;
- recency;
- source topic;
- manual blacklist flag;
- last-used timestamp.

## 11. Meme and card renderer

Use Pillow locally.

Initial formats:

1. top/bottom meme;
2. quote card;
3. clean caption card;
4. demotivator-style frame;
5. two-panel comparison when two compatible images exist.

Rendering requirements:

- automatic text wrapping;
- font fallback that supports Cyrillic;
- safe margins;
- no transparent unreadable text;
- size limits suitable for Telegram;
- deterministic temp-file cleanup;
- cache key based on source `file_unique_id` + normalized caption + template version.

Do not copy Sglypa's exact templates, wording or visual identity.

## 12. Polls and lightweight events

Events should emerge from local conversation where possible.

Examples of our own mechanics:

- "Момент чата" — bot turns a recent exchange into a short playful summary/card;
- "Архив проснулся" — resurfaces an old harmless local meme or quote after a long cooldown;
- "Дуэль мнений" — creates a two-option playful poll from two recently discussed alternatives;
- "Сегодняшний вайб" — chooses a harmless chat-wide theme from recent recurring words;
- "Кадр дня" — uses a recent image with an original caption when enough activity exists.

Avoid fabricating real claims about people. Clearly playful formats remain obviously playful.

## 13. Admin UX

`/fun` becomes the main inline panel.

Sections:

- status;
- behavior mode (`Спокойный` / `Живой` / `Активный`);
- autonomous text;
- memes/media;
- quotes/callbacks;
- polls/events;
- quiet hours;
- memory statistics;
- topic controls;
- emergency disable;
- clear memory.

Admin-only mutations remain permission-checked through Telegram chat member status.

Raw technical settings may exist as environment variables but should not be normal user-facing controls.

## 14. Storage model

Logical entities:

### `ent_chat_settings`

- chat_id PK
- enabled
- behavior_mode
- quiet_hours_start / quiet_hours_end / timezone
- feature toggles
- created_at / updated_at

### `ent_topic_settings`

- chat_id
- topic_id
- enabled override
- feature overrides
- last_autonomous_at
- rolling budget counters

### `ent_messages`

- id
- chat_id
- topic_id
- message_id
- user_id
- normalized text
- raw text or sanitized source text according to retention policy
- created_at
- eligibility flags

### `ent_media`

- id
- chat_id
- topic_id
- message_id
- user_id
- media_type
- file_id
- file_unique_id
- metadata JSON
- created_at
- last_used_at
- use_count
- blacklisted

### `ent_actions`

- id
- chat_id
- topic_id
- action_type
- trigger_message_id nullable
- source_memory_ids JSON/relational join
- status
- created_at
- metadata JSON

### `ent_generated_assets`

- id
- chat_id
- topic_id
- source_media_id nullable
- template
- telegram_file_id nullable
- cache_key
- created_at
- last_used_at

PostgreSQL may use JSONB for flexible metadata. SQLite stores equivalent JSON text through the shared storage interface.

## 15. Migration from current v1

Current `entertainment.db` must not simply be discarded.

Migration procedure:

1. detect existing SQLite tables;
2. read current chat settings and learned text rows;
3. normalize missing topic ids to `0`;
4. import into the selected v2 backend idempotently;
5. record a migration marker/version;
6. retain the old SQLite file until successful verification;
7. do not delete old data automatically during the first migration release.

If PostgreSQL is enabled later after the bot has accumulated more SQLite data, the migration must support an explicit one-time SQLite -> PostgreSQL import.

## 16. PostgreSQL integration

Use an async driver suitable for aiogram, preferably `asyncpg` directly for this bounded schema rather than introducing a large ORM unless migrations become hard to manage.

Requirements:

- connection pool initialized once during app startup;
- bounded pool size appropriate to a single Bothost bot instance;
- explicit timeouts;
- transaction around migration steps;
- indexes on `(chat_id, topic_id, created_at)` and media lookup columns;
- no database call should block the event loop;
- graceful pool close on shutdown.

The storage abstraction must keep tests runnable with SQLite without requiring a live PostgreSQL service.

## 17. Scheduler and lifecycle

No separate daemon.

The service owns one supervisor task created during startup.

It periodically examines only enabled chats/topics that have recent activity or a scheduled lightweight event. It should not continuously scan all historical rows.

On restart:

- settings and last-action timestamps restore from DB;
- ephemeral rolling counters can be rebuilt approximately from recent `ent_actions`;
- no pending action is blindly replayed;
- duplicate autonomous sends are prevented by persisted action records/idempotency tokens where needed.

## 18. Safety and moderation coexistence

Entertainment is downstream of chat allowlisting and must not bypass existing moderation.

The entertainment module must:

- never disable writers moderation;
- never re-send content that the bot itself had removed/blocked if that information is available;
- support a memory blacklist;
- escape generated HTML unless intentionally formatted;
- avoid impersonating users;
- distinguish explicit quote/callback features from newly generated speech;
- avoid generating factual accusations or sensitive claims about participants;
- keep admin-only destructive actions permission-checked.

## 19. Performance targets

For normal message observation:

- do not render images inline in the message handling hot path;
- keep storage writes small;
- batch/trim memory asynchronously where reasonable;
- normal messages should add negligible latency to unrelated bot handlers;
- expensive generation occurs only after an action candidate wins.

For Bothost resource control:

- bounded in-memory context windows;
- bounded DB queries;
- bounded Pillow image dimensions;
- TTL cache cleanup;
- no uncontrolled background task creation per message.

## 20. Testing strategy

Unit tests:

- chat/topic isolation;
- conversation phase derivation;
- candidate scoring and budget enforcement;
- no-copy novelty filter;
- quiet hours;
- repeated-action suppression;
- media eligibility;
- meme text wrapping/cache keys;
- admin permission helpers;
- SQLite storage contract.

Integration-style tests with fakes:

- message -> memory -> engine -> action;
- forum topic A never uses topic B context by default;
- disabled chat produces no actions;
- moderation handlers remain independently registered;
- scheduler restart does not duplicate actions;
- v1 SQLite migration is idempotent.

PostgreSQL contract tests can be optional in CI when a service is available; the core suite must still run without external infrastructure.

## 21. Rollout sequence

1. refactor v1 into package without changing visible behavior;
2. introduce storage interface and migration versioning;
3. add PostgreSQL backend and SQLite fallback;
4. add topic-aware memory/context;
5. implement autonomy engine and budgets;
6. add media memory;
7. add quote/callback generators;
8. add Pillow renderer and meme generators;
9. add polls/events;
10. upgrade `/fun` admin UI;
11. add optional AI provider interface, disabled by default;
12. tune behavior from real chat telemetry.

Each stage must preserve the ability to disable entertainment independently.

## 22. Operational configuration

Proposed environment variables:

```env
ENTERTAINMENT_CHAT_IDS=-1002619489118
DATABASE_URL=postgresql://...
ENTERTAINMENT_DB_FALLBACK_SQLITE=0
ENTERTAINMENT_DEFAULT_MODE=alive
ENTERTAINMENT_TIMEZONE=Europe/Moscow
ENTERTAINMENT_CACHE_DIR=/app/data/entertainment_cache
```

Exact Bothost-provided PostgreSQL variable names may differ; config should support mapping from the connection string Bothost provides rather than hardcoding vendor-specific secrets into the repository.

## 23. Acceptance criteria

The v2 architecture is complete when:

- the selected chat can run entertainment with PostgreSQL persistence on Bothost;
- SQLite local mode still works;
- forum topics have isolated working context;
- autonomous actions are state-driven and budget-limited;
- the bot can autonomously produce at least text, quote/callback, poll/event and local image meme/card actions;
- generated text is checked against source-message copying;
- media is reused through Telegram identifiers without bloating the DB;
- `/fun` exposes our own behavior modes and feature controls;
- entertainment can be disabled without affecting moderation/Lexicon/other modules;
- existing v1 entertainment memory is preserved through migration;
- tests cover the key isolation, persistence, scheduler and anti-spam invariants.

## 24. Design decision summary

- Product: our own autonomous chat personality, not a clone.
- Production DB: PostgreSQL when configured on Bothost.
- Development/fallback DB: SQLite behind the same storage interface.
- Runtime: one aiogram process, no Redis/worker requirement.
- Autonomy: phase/context/candidate engine, not one visible random percentage.
- Memory: per-chat + per-topic, with small chat-global culture layer.
- Media: Telegram `file_id` metadata, not database blobs.
- Rendering: local Pillow templates first.
- AI: optional adapter later, never required for baseline operation.

# Entertainment Culture Memory v1 — chronological chat memory, media culture and memes

## 1. Goal

Upgrade Entertainment from text-only memory into a topic-isolated chronological culture memory that can learn the *flow* of a Telegram conversation and reuse the chat's own language, emoji, stickers and image memes in context.

The intended experience is deliberately meme-friendly: the bot should feel as if it has lived in the chat, remembers recurring phrasing and media, and can remix that culture without merely copying old messages or spamming random stickers.

Success means:
- consecutive messages remain consecutive in memory and can influence generation as a sequence;
- reply relationships and conversational neighborhoods are preserved;
- text generation can learn from adjacent turns instead of receiving only a flat `list[str]` projection;
- old culture from the retained ~100k-event history can periodically re-enter generation through bounded historical windows rather than becoming dead archive data;
- Unicode emoji and emoji-only messages are remembered and can influence output;
- stickers, photos and animations are remembered by Telegram `file_id` and may be reused contextually without permanently downloading binary media;
- the bot can create new caption memes from a replied-to or remembered photo;
- all memory remains isolated by chat + topic;
- media actions remain governed by the existing autonomy budget and do not become spam;
- users can opt out of future learning and delete their own stored contributions without leaving duplicated raw text in derived media metadata.

## 2. Relationship to Generation v3

Culture Memory v1 is a storage/runtime foundation and must not be blocked by the separate Generation v3 implementation.

The rollout keeps compatibility in both directions:
- the current generator can continue receiving a text projection (`recent_texts`) from the new event stream;
- Culture Memory Phase B can already improve source selection through chronological windows;
- Generation v3 can later consume structured turns, reply anchors, author boundaries and chronological neighborhoods directly;
- no external AI provider is required by Culture Memory.

Culture Memory should improve the available source data even before Generation v3 lands.

## 3. Current baseline and problem

The current Entertainment storage persists text messages with:
- chat id;
- topic id;
- Telegram message id;
- user id;
- text;
- creation time.

However, the runtime generation contract exposes primarily `list[str]`. The generator therefore loses much of the conversational structure even though some source metadata exists in storage.

The current learning path also rejects non-text messages and text that does not satisfy the normal text-token rules. As a result:
- sticker messages are not part of Entertainment memory;
- photos/animations are not part of Entertainment memory;
- emoji-only messages are effectively excluded;
- reply relationships are not persisted for Entertainment;
- media is not associated with nearby text;
- old history outside the bounded recent generation sample is retained but rarely influences output;
- the bot cannot learn which sticker/meme tends to appear around which topic or conversational turn.

## 4. Scope

Culture Memory v1 includes four implementation phases under one architecture:

1. **Chronological Event Memory + privacy controls**
2. **Sequence/Culture Engine + historical windows + emoji behavior**
3. **Contextual sticker/photo/animation reuse**
4. **Meme Engine for captioned photo remixes**

The phases are implemented and verified sequentially so storage correctness lands before autonomous media behavior.

## 5. Non-goals

This phase does not add:
- cross-chat or cross-topic memory;
- face recognition or identity inference from images;
- OCR/image understanding as a dependency;
- heavyweight local vision or language models;
- permanent storage of Telegram image/sticker binaries;
- autonomous video editing;
- arbitrary internet image scraping;
- reaction harvesting as a required input signal;
- mandatory external AI APIs.

A future image/vision provider may enrich image semantics, but v1 must work without it.

## 6. Canonical memory model

### 6.1 New canonical event stream

Introduce a new `ent_memory_events` table rather than mutating the legacy text table into a polymorphic shape. This keeps migration reversible and avoids making the old `text NOT NULL` contract ambiguous.

Each stored event represents one supported human Telegram message in an Entertainment-enabled chat/topic.

Proposed logical model:

```text
MemoryEvent
  id                    internal sequence id
  chat_id               Telegram chat id
  topic_id              normalized topic id, 0 for no topic/general topic
  message_id            Telegram message id when available
  user_id               sender id
  event_type            text | emoji | sticker | photo | animation
  text                  textual message payload when present
  caption               media caption when present
  reply_to_message_id   Telegram message id replied to, when present
  file_id               reusable Telegram file id for media
  file_unique_id        stable Telegram media identity when available
  sticker_emoji         associated sticker emoji when available
  sticker_set_name      sticker pack name when available
  media_width           optional width
  media_height          optional height
  media_duration        optional animation duration
  is_forwarded          whether Telegram marks it as forwarded
  legacy_source_id      source id used only for idempotent legacy backfill
  metadata              backend-neutral structured metadata
  created_at            event timestamp
```

SQLite stores structured metadata as JSON text. PostgreSQL uses JSONB.

No raw neighborhood/context copy is persisted in another user's media row. Context is resolved from the canonical event stream at read time. This is important for `/fun_delete_me`: deleting a user's events must not leave their raw text duplicated elsewhere.

### 6.2 Event types

`TEXT`
- normal text with useful textual content;
- may contain Unicode emoji;
- storage accepts short conversational messages that are useful as sequence boundaries even when they are too short to independently trigger generation.

`EMOJI`
- Unicode emoji-only or near-emoji-only message;
- stores the original emoji string;
- custom emoji ids may be recorded in metadata when exposed by Telegram, but outbound custom-emoji reuse is not required for v1.

`STICKER`
- stores `file_id`, `file_unique_id`, sticker emoji and set name when available;
- regular, animated and video stickers may all be reused through Telegram file ids;
- no sticker binary download is needed for ordinary reuse.

`PHOTO`
- stores the `file_id`/`file_unique_id` of the largest useful photo size plus dimensions and caption;
- binary bytes are not persisted.

`ANIMATION`
- stores Telegram animation/GIF file ids and optional caption/duration;
- reused directly through Telegram when selected.

Videos, voice notes, documents and audio are out of scope for v1 and ignored by Culture Memory unless a later phase explicitly adds them.

## 7. Ordering and conversation structure

The event stream must preserve actual conversation order.

Ordering for reads is deterministic:
1. `created_at`;
2. Telegram `message_id` when available;
3. internal event `id` as a final tiebreaker.

For modern Telegram messages, `message_id` provides a stable within-chat order. Legacy imported rows without message ids fall back to timestamp + internal id.

### 7.1 Reply links

Persist `reply_to_message_id` whenever available.

A storage index on `(chat_id, message_id)` allows the culture layer to resolve a reply target without mixing chats. The resolved target must still belong to the same topic before it is used as topic-local generation context.

If the replied-to event is unavailable or already pruned, the relationship is simply unresolved; ingestion must not fail.

### 7.2 Conversation runs

The culture engine groups neighboring events into bounded conversation runs using:
- same chat/topic;
- chronological adjacency;
- a configurable time-gap boundary (initially around 8 minutes);
- explicit reply links as a strong relation signal.

Runs are an in-memory generation/retrieval concept, not another persistent table in v1.

## 8. Sequence learning semantics

The bot should learn from consecutive messages without naively concatenating every user's tokens into one giant Markov chain.

Use three relation levels.

### 8.1 Intra-message phrase relations

Text within one human message remains the strongest lexical/phrase source.

### 8.2 Same-user consecutive continuation

When the same person sends several adjacent text messages in a short interval, the engine may treat them as a stronger continuation sequence. This supports the common Telegram style of splitting one thought across multiple messages.

### 8.3 Cross-user turn relations

When different people speak consecutively, learn *turn-to-turn association* rather than raw token fusion.

Example:
- message A contains topic/phrase X;
- message B follows or replies and contains phrase Y;
- later X-like context may increase the probability of Y-like phrase fragments.

This preserves the meme value of real chat adjacency while reducing grammatical garbage from crossing author boundaries at arbitrary words.

Explicit reply links receive more weight than accidental adjacency.

## 9. Historical window sampling

The ~100k retained events must not become an archive that never affects generation.

Every generation request may use two bounded sources:

1. **Recent window** — the normal recent generation sample, approximately up to the configured 1,500 text-capable events.
2. **Historical windows** — several small contiguous windows sampled from older retained history in the same chat/topic.

Historical sampling requirements:
- no full 100k scan;
- preserve adjacency inside each sampled window;
- select only a small bounded number of windows/events per generation;
- deterministic under a supplied seeded RNG in tests;
- keep recent context weighted more strongly than random historical culture;
- do not mix events from another chat/topic;
- ignored/deleted users disappear naturally because their canonical events no longer exist.

A practical initial target is roughly 4–8 historical windows of 10–25 events each, configurable internally after benchmarks.

This means an old phrase/sticker/meme can periodically return months later without loading the whole retained corpus into memory on every message.

## 10. Context neighborhoods for media

A sticker/photo/animation needs textual context to be reusable intelligently months later, but that context must not be duplicated into persistent raw-text hints.

For each bounded media candidate, build its context neighborhood at retrieval time from:
- its own caption;
- replied-to event when resolvable;
- approximately 3–5 neighboring useful text/emoji events before it;
- optionally a small number of immediately following events when available.

Storage should expose a batched neighborhood query so media ranking does not perform one SQL round trip per candidate.

The resulting context representation exists only for the selection request and is discarded afterward.

Generation v3 may later apply lemmas/morphology to these neighborhoods; Culture Memory v1 can begin with the current tokenizer/surface forms.

## 11. Storage migration strategy

### 11.1 Why a new table

The existing `entertainment_messages` table remains a safe rollback source and has stable production behavior. Culture Memory therefore creates `ent_memory_events` and performs an idempotent migration rather than destructively rewriting the old table.

### 11.2 Backfill

One migration key backfills existing `entertainment_messages` rows as `TEXT` events.

The migration:
- preserves chat/topic/user/text/message id/created_at;
- stores the old row id as `legacy_source_id`;
- has a unique partial index on `legacy_source_id` when non-null;
- is idempotent;
- processes in bounded batches;
- does not delete the old table;
- records completion in `ent_schema_migrations`;
- never duplicates a legacy source row on restart.

### 11.3 Compatibility cutover

Phase A uses a compatibility projection:
- canonical new reads for Culture Memory use `ent_memory_events`;
- current text-generation APIs expose `recent_texts(...)` projected from TEXT and caption-bearing events;
- current action/activity behavior is verified for parity before switching all read paths;
- the legacy text table is retained for one release cycle as rollback data, then can be removed in a later cleanup PR.

No destructive migration is part of Culture Memory v1.

## 12. Retention

The existing Entertainment memory policy remains the primary cap.

Default target:
- approximately 100,000 culture events per topic, with buffered/batch pruning rather than an expensive full retention delete after every insert;
- generation reads only bounded recent + historical samples;
- media references count as events but are tiny compared with binary media because only Telegram ids/metadata are stored.

The bot must never download and retain 100,000 media files.

When pruning:
- oldest events are removed first;
- reply links may become unresolved safely;
- no cascading delete of Telegram content occurs;
- selection code treats missing neighbors/reply targets as normal.

## 13. Privacy and user control

Introduce `ent_memory_preferences` keyed by `(chat_id, user_id)`.

Logical fields:
- `remember_enabled` (default true to preserve current bot behavior);
- `updated_at`.

Commands:

`/fun_ignore_me`
- future events from this user are not persisted by Culture Memory in the current chat;
- does not retroactively delete already stored events.

`/fun_remember_me`
- re-enables future learning for the current chat.

`/fun_delete_me`
- deletes that user's Culture Memory events in the current chat across all topics;
- removes their remembered media references because those are events too;
- does **not** delete Telegram messages from the chat;
- because media context is derived on read rather than persisted as copied text, deleted raw text cannot remain embedded in another user's media event.

`/fun_forget`
- existing admin behavior evolves to clear the full current Culture Memory scope, not only text projection.

Privacy checks happen before storage.

## 14. Ingestion pipeline

`EntertainmentLearningMiddleware` remains non-consuming: it observes messages and then allows normal bot handlers to continue.

`EntertainmentService.observe_message()` becomes an event classifier rather than a text-only gate.

Pipeline:
1. verify chat is in Entertainment allowlist;
2. verify group/supergroup and human sender;
3. check user's memory preference;
4. classify message into supported event type;
5. normalize topic and reply link;
6. extract text/emoji/media metadata;
7. persist the canonical event idempotently;
8. update active-topic marker;
9. invoke autonomous evaluation only when appropriate.

Not every stored event must trigger text generation. A sticker/photo can update activity/context without forcing a text reply.

### 14.1 Activity semantics

Supported human culture events are real chat activity.

During Phase A, existing text-based counters stay unchanged until storage parity is verified. Once the canonical event stream is authoritative:
- conversation phase/activity snapshots may count supported human events, not only long text;
- generation readiness still requires enough text-capable source events, so a sticker flood cannot pretend to be a rich text corpus;
- post-action human-activity budgets may count supported human events because emoji/sticker responses are still human participation.

This cutover is covered by explicit regression tests so autonomy frequency does not accidentally increase.

## 15. Emoji culture

### 15.1 Learning

Extract Unicode emoji from:
- normal text events;
- emoji-only events;
- sticker-associated emoji.

Learn co-occurrence with nearby words/lemmas and conversation runs during generation/retrieval.

Do not create a global emoji dictionary across chats.

### 15.2 Output

The generator may:
- append 0–2 contextually learned emoji to generated text;
- occasionally emit a short emoji-only response when autonomy selects that action type;
- avoid repeating the same emoji signature across recent bot actions.

Emoji is a style signal, not a mandatory decoration on every message.

## 16. Contextual media retrieval

Add a `MediaCultureSelector` independent from Telegram sending.

Inputs:
- current chat/topic;
- current conversation text/lemmas;
- optional direct trigger/reply target;
- recent bot actions;
- bounded recent and historical media candidates;
- batched context neighborhoods for those candidates.

Candidate score combines:
- overlap with caption + derived neighborhood;
- overlap with trigger text for direct replies;
- reply-chain relation where available;
- recency decay;
- historic occurrence frequency;
- diversity bonus for media not recently used by the bot;
- penalty for forwarded media when stronger local media exists;
- hard exclusion of media from another chat/topic.

Candidate acquisition should combine recent media with a bounded historical sample so old memes can return without scanning all retained events.

No semantic vector database is required.

## 17. Sticker behavior

The bot may autonomously send a remembered sticker when selected by the normal autonomy engine.

Rules:
- send by Telegram `file_id`;
- never download merely to resend;
- preserve topic/thread id;
- record the action only after successful send;
- if Telegram rejects/stales a `file_id`, suppress that media candidate for a bounded period and fall back instead of retrying repeatedly;
- do not reuse the same `file_unique_id` in a recent bot-media window;
- media actions share the existing global Entertainment action budget.

Sticker reuse must not create a second independent spam scheduler.

## 18. Photo and animation reuse

Photos and animations follow the same contextual selector.

For direct reuse:
- use Telegram `file_id`;
- v1 normally sends media without copying the original human caption verbatim;
- do not forward original sender attribution;
- record the action in `ent_actions` only after successful send.

Media selection failure falls back to text/no action rather than breaking the supervisor loop.

## 19. Meme Engine

### 19.1 Purpose

Create new meme variants from the chat's own remembered/replied photos and local language without requiring a remote image-generation model.

### 19.2 Source selection

Priority:
1. photo in the message being replied to;
2. recent relevant photo in the same topic;
3. older contextual photo selected from Culture Memory.

A user-provided reply-photo is never copied into another chat/topic.

### 19.3 Caption generation

Meme caption text comes from the local text/culture engine with stronger novelty requirements.

It must not simply reuse the original media caption or copy one source message unchanged.

Initial styles:
- classic top/bottom caption;
- demotivator-style frame/caption.

### 19.4 Rendering

Use Pillow as a lightweight dependency.

For a new meme:
- fetch the selected Telegram photo only at render time;
- render in memory or a temporary file;
- bound image dimensions before processing;
- send the resulting image;
- delete temporary bytes/files after sending;
- never persist the rendered binary in Culture Memory unless Telegram returns a reusable sent-message `file_id` and a later phase explicitly captures it.

A render/download failure must degrade gracefully to the original text/media action path.

## 20. Autonomy integration

Extend `EntertainmentActionType` with media-aware actions, for example:
- `EMOJI_REPLY`;
- `STICKER_REUSE`;
- `PHOTO_REUSE`;
- `ANIMATION_REUSE`;
- `MEME_REMIX`.

All actions remain under the existing behavior-mode budgets.

Additional anti-spam constraints:
- at most one autonomous media action in a bounded media cooldown window;
- no identical `file_unique_id` in the recent bot-media history;
- PEAK phase continues to suppress intrusive autonomous behavior unless it is a direct contextual reply;
- direct user-triggered commands may bypass autonomous timing budgets but still use novelty/repetition guards.

Do not create another background task beyond the existing Entertainment supervisor.

## 21. `/fun` UX

Extend the status panel to show useful memory counters for the current topic:
- total culture events;
- text events;
- emoji events;
- stickers;
- photos/animations.

Keep the panel compact.

Add privacy commands to help text.

Optional admin toggles may be introduced only if needed during implementation:
- autonomous media on/off;
- meme remix on/off.

They should default to conservative values during rollout.

## 22. Storage contract changes

Introduce typed storage APIs rather than leaking SQL into the service.

Target contract additions:

```text
add_event(event)
recent_events(chat_id, topic_id, limit)
recent_texts(chat_id, topic_id, limit)
sample_event_windows(chat_id, topic_id, window_count, window_size, rng_seed)
get_event_by_message_id(chat_id, message_id)
recent_media(chat_id, topic_id, limit, kinds)
media_neighborhoods(chat_id, topic_id, event_ids, radius)
memory_counts(chat_id, topic_id)
get_memory_preference(chat_id, user_id)
set_memory_preference(chat_id, user_id, enabled)
delete_user_memory(chat_id, user_id)
```

The existing `recent_messages(...)` compatibility method may delegate to `recent_texts(...)` for one release cycle.

PostgreSQL and SQLite implementations must remain behaviorally equivalent.

## 23. Indexing

PostgreSQL/SQLite indexes should support:
- `(chat_id, topic_id, created_at DESC, id DESC)` for chronology;
- `(chat_id, message_id)` for reply resolution;
- `(chat_id, topic_id, event_type, created_at DESC)` for bounded media retrieval;
- `(chat_id, user_id)` for user-memory deletion;
- `(chat_id, user_id)` primary/unique path for preferences;
- unique `(chat_id, message_id)` where message id is non-null to make ingestion idempotent;
- unique `legacy_source_id` where non-null for migration idempotency.

Do not add unbounded full-text indexes in v1.

## 24. Failure handling

Storage:
- event ingestion is idempotent for duplicate Telegram updates;
- one malformed media event must not crash message dispatch;
- migration failures follow the existing PostgreSQL-first startup policy rather than silently creating split-brain storage.

Telegram media:
- invalid/stale `file_id` -> suppress candidate and fall back;
- failed download for meme -> fallback, no persistent temp file;
- failed send -> do not record successful action.

Culture generation:
- insufficient relevant media -> choose text or no action;
- no good text candidate -> do not force a meme caption;
- sampled historical windows are optional enrichment, never a reason to fail the generation request.

## 25. Observability

Add structured logs/counters without logging full private chat contents:
- event ingested by type;
- event ignored due to privacy/preferences;
- recent/historical culture window sizes;
- media candidate count;
- selected media type;
- stale media ids;
- media/meme send success/failure;
- meme render latency;
- user-memory deletion counts;
- migration/backfill counts.

Never log BOT_TOKEN, DATABASE_URL, full media payloads or full historical corpus.

## 26. Performance constraints

Bothost compatibility remains mandatory.

Targets:
- no model downloads;
- no GPU;
- no persistent binary media cache;
- no full 100k-event scan on each message;
- recent generation uses the bounded configured sample;
- historical culture enters through small sampled contiguous windows;
- media retrieval uses bounded indexed queries plus batched neighborhood lookup;
- meme rendering is on-demand and bounded in dimensions;
- no new supervisor/background worker.

## 27. Testing strategy

### 27.1 Storage tests
- round-trip every event type in PostgreSQL and SQLite;
- chronological ordering;
- duplicate Telegram update idempotency;
- reply lookup with topic validation;
- migration/backfill idempotency;
- retention pruning;
- memory counters;
- privacy preference persistence;
- delete-user-memory removes text/media contributions only for that user/chat;
- deleted user text is not retained in another event as duplicated neighborhood text.

### 27.2 Ingestion tests
- short normal text can be remembered as conversation context even when it is not enough to trigger generation;
- emoji-only event stored;
- sticker metadata stored;
- largest photo variant selected;
- animation metadata stored;
- unsupported media ignored;
- bots ignored;
- opted-out user ignored;
- reply link captured;
- no cross-topic pollution.

### 27.3 Culture/sequence tests
- same-user adjacent split messages form a strong continuation relation;
- cross-user adjacency influences phrase/turn selection without raw arbitrary token concatenation;
- explicit replies outrank accidental adjacency;
- recent context receives more weight than historical windows;
- deterministic historical sampling can surface an old retained phrase under fixed seeds;
- no full-history scan is used by the generation path;
- exact source replay is still rejected.

### 27.4 Emoji/media selector tests
- contextual sticker beats unrelated sticker under fixed seeds;
- an older historically sampled relevant sticker can beat an unrelated recent sticker;
- recent duplicate sticker is suppressed;
- media never crosses chat/topic;
- stale file id is demoted after send failure;
- emoji selection reflects local co-occurrence rather than global frequency only.

### 27.5 Meme tests
- replied photo has source priority;
- generated caption passes novelty rules;
- Pillow renderer bounds dimensions;
- temporary file/buffer cleanup occurs on success and failure;
- failed render falls back safely;
- no permanent binary is stored.

### 27.6 Integration tests
- middleware observes media without consuming legacy handlers;
- autonomy records action only after successful send;
- existing text autonomy remains functional during migration;
- activity-budget cutover does not increase action frequency beyond configured behavior budgets;
- existing behavior budgets remain authoritative;
- PostgreSQL integration job remains green.

## 28. Rollout phases

### Phase A — Chronological Event Memory + privacy
- event model/table;
- migration/backfill;
- text compatibility projection;
- reply links;
- emoji/media ingestion;
- privacy commands;
- counters/tests;
- no autonomous media sending yet.

### Phase B — Sequence/Culture Engine + historical windows + emoji
- conversation-run extraction;
- adjacent-message relations;
- bounded historical window sampler;
- emoji co-occurrence/style output;
- current text generator consumes improved sequential context where possible;
- activity-count cutover after parity tests;
- no media reuse until selector tests are green.

### Phase C — Contextual media reuse
- bounded recent + historical media acquisition;
- batched media neighborhoods;
- media selector;
- sticker/photo/animation actions;
- stale-file handling;
- shared autonomy budgets;
- conservative rollout toggles.

### Phase D — Meme Engine
- Pillow dependency;
- replied/remembered photo source selection;
- caption generation;
- classic + demotivator renderers;
- temp cleanup and performance tests.

Each phase should ship as its own PR after verification rather than becoming one giant high-risk merge.

## 29. Interaction with existing Generation v3 design

Culture Memory exposes structured events without forcing Generation v3 to land first.

When Generation v3 is implemented, it can consume:
- structured recent text turns;
- sampled historical conversation windows;
- reply target text;
- author boundaries;
- time gaps;
- emoji associations;
- media neighborhoods.

Generation v3 must not become responsible for media persistence or Telegram sending; those remain Culture Memory/service responsibilities.

## 30. Acceptance criteria

Culture Memory v1 is complete when:
- existing text history is backfilled idempotently into the new event stream;
- new human text/emoji/sticker/photo/animation events are stored in chronological order per chat/topic;
- reply relationships are persisted when available;
- user opt-out/delete controls work for both PostgreSQL and SQLite;
- deleting user memory does not leave duplicated raw text in persisted media-context metadata;
- current text generation continues working through the compatibility projection;
- old retained history can re-enter generation through bounded chronological windows without full-history scans;
- sequence logic demonstrably uses adjacent turns without arbitrary cross-author token fusion;
- contextual emoji/sticker/media selection passes deterministic relevance tests;
- repeated media is suppressed;
- media never crosses chat/topic boundaries;
- meme rendering works from reply/remembered photos without persistent binary storage;
- media and meme actions remain inside the existing autonomy budget;
- PostgreSQL integration stays green;
- no new failures appear outside the repository's known unrelated baseline;
- no mandatory external API or secret is introduced.

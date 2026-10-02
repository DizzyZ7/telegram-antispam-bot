# Entertainment Culture Memory Phase C — Contextual Media + Bootstrap Learning

Date: 2026-10-02
Status: design approved in chat, awaiting written-spec review
Base: Culture Memory Phase B (`f908c276014a4d16a8ab65377970b5fd8f1ba076`)

## 1. Goal

Phase C makes Entertainment feel like a real long-lived participant in a Telegram chat by extending Culture Memory in two directions:

1. **Bootstrap Learning** — while a topic corpus is still small, remember a much broader slice of chat culture: short messages, human commands such as `/spawn`, and messages/media produced by other bots. These events remain stored after bootstrap; only their generation weight changes as the human corpus grows.
2. **Contextual Media Memory** — reuse remembered stickers, photos and animations/GIFs by Telegram `file_id` when the current conversation context strongly matches the context in which that media historically appeared.

The feature must stay topic-isolated, bounded, restart-safe, privacy-aware and non-spammy. It must not require an external LLM, vision API, GPU, or permanent media downloads.

## 2. Non-goals

Phase C does **not**:

- generate new image files or caption memes; that belongs to the later Meme Engine phase;
- inspect image pixels or call vision models;
- increase autonomous action frequency above existing BehaviorMode budgets;
- make media responses mandatory;
- train on the Entertainment bot's own generated output;
- merge data across chats or forum topics;
- permanently download or store Telegram media binaries.

## 3. Canonical event model additions

`MemoryEvent` remains the canonical chronological record. Add safe provenance fields needed for learning policy:

- `sender_is_bot: bool = False`
- `is_command: bool = False`

`user_id` continues to store Telegram sender id, including another bot's user id when `sender_is_bot=true`.

Existing fields remain unchanged, including `reply_to_message_id`, `file_id`, `file_unique_id`, sticker metadata, caption and `is_forwarded`.

### 3.1 Command classification

A text event is a command when its first non-space character sequence begins with `/` and has a Telegram-style command token, e.g.:

- `/spawn`
- `/raid boss`
- `/roll@SomeBot 20`

Commands are stored as ordinary chronological text events with `is_command=true`; they are no longer discarded from Culture Memory.

### 3.2 Sender classification

Human users and other Telegram bots may be stored. The Entertainment bot itself is always excluded from learning to prevent self-training loops.

Service/system messages without a normal sender identity remain excluded.

## 4. Bootstrap Learning policy

### 4.1 Bootstrap threshold

Define:

`ENTERTAINMENT_BOOTSTRAP_TEXT_EVENT_THRESHOLD=10000`

Default: `10000` per `chat_id + topic_id`.

The threshold is based on canonical **textual culture events** (`TEXT` + `EMOJI`), not total media count. This prevents a sticker-heavy topic from prematurely exiting bootstrap.

### 4.2 What is stored

For an allowed Entertainment chat/topic, Culture Memory stores:

- normal human text;
- short human text;
- emoji-only messages;
- human commands such as `/spawn`;
- stickers/photos/animations from humans;
- text/emoji/media sent by **other bots**;
- captions and reply relations already supported by Phase A.

The Entertainment bot's own messages are excluded.

Forwarded events may remain represented by the existing `is_forwarded` metadata when ingested, but **forwarded media is never eligible for autonomous media reuse** and forwarded text receives no special bootstrap boost.

### 4.3 Activity and autonomy isolation

Broader storage must not change participation frequency:

- other-bot events do not count as human activity;
- commands do not increment the legacy human-message activity path;
- media-only events do not increment the text activity path;
- existing BehaviorMode action budgets remain the only autonomous frequency control.

Therefore a raid/spawn bot may enrich Culture Memory without causing Entertainment to answer every bot event.

### 4.4 Generation weights

All eligible events remain stored after the topic passes 10k textual events.

Before threshold:

- human ordinary text: normal Phase B weight;
- human commands: normal bootstrap weight;
- other-bot text/commands: normal bootstrap weight;
- recent context still outweighs historical windows.

After threshold:

- human ordinary text remains normal weight;
- human commands stay usable but receive a reduced source weight;
- other-bot text/commands stay usable but receive a reduced source weight;
- they are never deleted merely because bootstrap ended.

This preserves old `/spawn` jokes and bot-specific local memes without letting machine-generated traffic dominate a mature human corpus.

The exact multiplier belongs in implementation constants/tests rather than persisted rows. Recommended default post-bootstrap weight: approximately 0.35–0.5 of normal human source weight.

## 5. Contextual Media Memory

### 5.1 Supported media

Autonomous reuse supports:

- `STICKER` via Telegram `send_sticker(file_id)`;
- `PHOTO` via `send_photo(file_id)`;
- `ANIMATION` via `send_animation(file_id)`.

No media download is required for reuse.

### 5.2 Media context reconstruction

Do not persist duplicated neighboring text in media metadata. Build media context on read from the chronological event stream.

For each media candidate, derive bounded context from:

- its own caption, if any;
- sticker emoji and sticker-set metadata as weak semantic signals;
- preceding/following text in the same conversation run;
- explicit `reply_to_message_id` relationships;
- same-author local sequence;
- the current live conversation context from Phase B;
- sampled historical windows already used by the Culture Engine.

This keeps `/fun_delete_me` correct because user text has only one canonical representation.

### 5.3 Candidate eligibility

A media event is eligible only when:

- it belongs to the same `chat_id + topic_id`;
- `file_id` and `file_unique_id` are present;
- it is not forwarded;
- it is not the Entertainment bot's own event;
- it is not inside the anti-repeat cooldown;
- its media type is supported;
- its contextual relevance crosses the media-type threshold.

Media from other bots is allowed if it passes the same contextual rules.

### 5.4 Ranking

Create a pure `MediaCandidate` scorer. Candidate score should combine:

- current-context term overlap;
- caption overlap;
- nearby historical text overlap;
- reply-link match bonus;
- sticker emoji relevance;
- recency;
- repeated historical use in similar context;
- source-type weight (human vs other bot, bootstrap-aware);
- annoyance penalty / repeated-use penalty.

Recent Culture Memory must have more weight than historical sampled windows.

### 5.5 Media thresholds

Use different minimum thresholds:

- stickers: lowest threshold;
- animations/GIFs: medium threshold;
- photos: highest threshold.

Photos must require a clear contextual match. The bot must never choose a random remembered photo merely because a text action was available.

## 6. Autonomous action integration

Reuse the existing `EntertainmentActionType.MEMORY_CALLBACK` for contextual media.

A media callback consumes **one normal autonomous action slot** from the existing BehaviorMode budget. It does not have a separate media budget that could increase total frequency.

Rules:

- in `PEAK`, non-direct media callbacks are suppressed just like non-direct text;
- two non-direct `MEMORY_CALLBACK` actions may not happen consecutively;
- if media ranking returns no eligible candidate, continue with the existing Phase B text path;
- a media candidate must never force an otherwise-ineligible autonomous action;
- supervisor behavior remains bounded to known active topics.

## 7. Anti-repeat policy

Default media anti-repeat window:

`ENTERTAINMENT_MEDIA_REPEAT_COOLDOWN_SECONDS=21600`

Default: 6 hours per `chat_id + topic_id + file_unique_id`.

Recent action metadata is sufficient to enforce it; no new unbounded tracking table is required unless implementation evidence proves otherwise.

Store in action metadata only safe diagnostics such as:

- `media_type`
- `media_file_unique_id`
- bounded numeric relevance score / score bucket
- source class (`human` or `other_bot`)
- `source=message|supervisor`

Do not persist corpus snippets or neighboring raw text into action metadata.

## 8. Sending behavior

For autonomous reuse:

- sticker: send only the sticker;
- photo: send the remembered `file_id` without copying the original caption;
- animation: send the remembered `file_id` without copying the original caption.

When responding inside a forum topic, preserve `message_thread_id`.

When the normal message-triggered path is used, media may be sent as a reply when Telegram API semantics are reliable; otherwise topic-scoped send is acceptable. Tests must pin the selected behavior.

Telegram send failure must be isolated:

- log safe identifiers only;
- do not record a successful action if send failed;
- do not crash the supervisor loop;
- text fallback may be attempted only if it does not create a second autonomous action or bypass the original budget decision.

## 9. Privacy behavior

Existing privacy controls continue to apply to human events:

- `/fun_ignore_me` blocks new human memory ingestion, including commands and media;
- `/fun_remember_me` re-enables it;
- `/fun_delete_me` removes that human user's canonical events, including commands and media.

Messages from other bots are treated as chat culture rather than user privacy state and are not controlled by a human user's opt-out row.

Admin `/fun_forget` still clears the current topic's canonical Culture Memory regardless of sender type.

## 10. Storage and query constraints

Do not scan all ~100k events per generation.

Phase C reuses Phase B bounded retrieval:

- recent event slice bounded by `GENERATION_SAMPLE_LIMIT`;
- deterministic small historical windows;
- media ranking only across candidates present in those bounded inputs, optionally plus a separately bounded media lookup if tests show recent/historical windows under-sample media.

If a separate media lookup is needed, it must have an explicit hard limit and topic index; never `SELECT *` over the full topic corpus.

SQLite and PostgreSQL must expose equivalent semantics.

## 11. Suggested module boundaries

Keep responsibilities isolated:

- `entertainment/memory.py` — sender/command classification only;
- `entertainment/culture.py` — Phase B text/emoji context and bootstrap weighting inputs;
- `entertainment/media_culture.py` — pure media candidate extraction/scoring/ranking;
- `entertainment/service.py` — orchestration, budget decision and Telegram send only;
- storage layer — bounded chronological reads and counts, no ranking policy.

Do not put media scoring logic directly into `service.py`.

## 12. Observability

Safe logs/counters may include:

- bootstrap textual event count and whether scope is in bootstrap;
- number of media candidates considered;
- chosen media type;
- chosen score bucket;
- send success/failure;
- whether fallback text path was used.

Never log:

- `file_id` if not needed for debugging;
- message/caption corpus text;
- DSN/credentials;
- private neighboring message content.

`file_unique_id` may be stored in action metadata for anti-repeat but should not be emitted in routine logs.

## 13. Testing requirements

TDD coverage must include at least:

1. command classifier stores `/spawn`, `/spawn@Bot`, arguments and short commands;
2. other-bot sender events are ingested while the Entertainment bot itself is rejected;
3. human privacy opt-out still blocks human commands/media;
4. bot/command events do not change human activity/autonomy cadence;
5. bootstrap threshold is per topic and based on text+emoji count;
6. post-bootstrap bot/command source weights are reduced, not removed;
7. SQLite/PostgreSQL parity for new provenance fields/counts;
8. topic isolation for bot messages and media;
9. media scorer prefers contextually matching sticker/GIF over unrelated candidates;
10. photos require the strictest relevance threshold;
11. forwarded media is never selected;
12. 6-hour `file_unique_id` anti-repeat;
13. no consecutive non-direct media callback;
14. no random media in `PEAK`;
15. media consumes the same action budget as text;
16. correct `send_sticker`, `send_photo`, `send_animation` calls with topic id;
17. original photo/GIF caption is not copied on reuse;
18. send failure does not record success or kill supervisor;
19. no full-history scan in generation/media selection;
20. legacy/minimal storage fallback remains compatible where still supported.

## 14. Acceptance criteria

Phase C is complete when:

- allowed topics remember human commands and other-bot culture without self-training;
- `/spawn`-style material can influence generated combinations;
- crossing 10k textual events changes weighting, not retention;
- contextual stickers/photos/GIFs can be autonomously reused by `file_id`;
- unrelated/private-looking photos are not randomly resurfaced;
- media does not increase total autonomous action frequency;
- media repeats are suppressed for 6 hours;
- privacy/topic isolation remain correct;
- SQLite and PostgreSQL behavior is equivalent;
- no permanent media binary storage or external AI dependency is introduced;
- existing Phase B text/emoji generation remains the fallback when no media candidate is good enough.

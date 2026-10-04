# Entertainment Generation v3 — Chat-Only Language Engine

Generation v3 is the default local text engine for Entertainment. It is designed to synthesize new topic-relevant chat phrases from the same bounded Culture Memory snapshot that Phase C already provides, without loading external text corpora or scanning the full long-term history on every request.

## Source boundary

The engine may use only:

- Culture Memory events from the current `chat_id + topic_id` scope already selected by the Phase C snapshot;
- the bounded recent conversation context from that same scope;
- the exact current trigger text when the action is a direct reply;
- recent outputs of this Entertainment bot for replay suppression.

The engine does **not** load stories, fairy tales, books, Ficbook texts, Lexicon dictionaries, internet text, neighboring forum topics, other chats, external AI APIs, downloaded language models, vector databases or GPU workers.

The writers-chat topic scopes `292358`, `14637` and `42817` remain behind the existing hard Entertainment denylist. They stop before Culture Memory reads, generation, generation-status storage reads and admin lookups.

## Synthesis pipeline

1. Build a `TopicAnchor` from at most the latest 40 context messages.
2. In direct-reply mode, remove exact legacy copies of the trigger from context weighting and add the trigger once as the strongest explicit signal.
3. Normalize Culture messages into distinct source identities so repeated weighting of one source cannot imitate multi-source composition.
4. Select at most 128 distinct sources for synthesis, preferring current-topic relevance, then weak Culture weight and recent source order.
5. Build bounded local 5→4→3→2→1 token transition indexes only from that synthesis working set.
6. Generate candidates from:
   - deterministic bounded phrase-chunk bridges across the most topical distinct sources;
   - controlled source crossovers;
   - 5→1 transition backoff.
7. Hard-reject unsafe candidates against the **complete distinct source set from the input snapshot**, not only the bounded synthesis set.
8. Rank accepted candidates by current topic fit, direct-trigger coverage, phrase support, source composition, soft morphology and bounded structural signals.
9. Only after text validation succeeds, apply the existing local emoji style layer.

`pymorphy3` is a soft linguistic signal only. Commands, usernames, English tokens and slang remain valid when morphology is unavailable or uncertain. Token analysis is held in a bounded 4096-entry LRU cache.

## Anti-copy invariants

Generation v3 fails closed rather than weakening copy constraints when the corpus is too small.

- exact source replay is rejected;
- exact recent bot-output replay is rejected;
- repeated trigram loops are rejected;
- the longest contiguous overlap with any one source may not exceed 6 words;
- the overlap with any one source may not exceed 70% of candidate words;
- candidates of 6+ words require support from at least two distinct normalized source identities;
- repeated weighted copies of the same source never count as independent sources;
- reducing the synthesis working set never reduces the source set used for anti-copy validation.

The final direct-reply selection order is intentionally bounded and explicit: maximum supported trigger coverage first, then the strongest phrase-support band, then the strongest topical-relevance band, with controlled variation only inside that safe high-quality set.

## Bounded runtime behavior

Long-term retention may hold roughly 100,000 canonical events per forum topic, but Generation v3 does not scan that entire history. It consumes the existing Phase C bounded snapshot: up to `ENTERTAINMENT_GENERATION_SAMPLE_LIMIT` recent events plus the small deterministic historical windows already used by Culture Memory.

Candidate generation is additionally bounded:

- synthesis working set: at most 128 distinct sources;
- target candidate count: 48;
- accepted constructor range: 32–64;
- raw candidate processing cap: 160;
- deterministic structured bridge source cap: 24 topical distinct sources;
- deterministic structured candidate cap: 64.

The 128-source limit bounds transition-model construction and randomized synthesis work. Anti-copy validation still sees the full distinct input snapshot, so the performance limit is not a safety relaxation.

No schema migration is required for Generation v3 or its production observability.

## Contextual remembered media and greetings

Culture Memory media remains reusable inside the exact `chat_id + topic_id` scope instead of behaving like one-shot echo memory. Stickers, photos and animations still require a valid Telegram `file_id`, are never borrowed from another topic/chat, and forwarded media remains excluded from callback selection.

Media selection now preserves contextual ranking while adding controlled variety:

- candidates are scored against the latest bounded same-topic context;
- Russian word forms are matched through the existing fail-soft `pymorphy3` analysis while surface forms remain available for English, slang, usernames and meme vocabulary;
- a clearly stronger contextual candidate continues to win;
- near-equal high-quality candidates are selected with weighted variation, so one sticker does not permanently dominate the pool;
- an exact media item used during the last 5 minutes is temporarily suppressed;
- the existing 6-hour media window is a **soft diversity window**, not a hard ban: previously used media gradually regains weight and can be reused long before six hours when the pool/context makes it appropriate;
- the existing rule preventing consecutive non-direct media callbacks remains in place, so variety does not become media spam.

Explicit morning/night greetings have a separate bounded social-reaction path. It recognizes short intentional forms such as `доброе утро`, `утро`, `спокойной ночи`, `споки` and `гн`, while ordinary sentences that merely mention morning/night are not treated as greetings.

Greeting behavior is intentionally restrained:

- response probability: 40%;
- per-topic greeting cooldown: 20 minutes;
- if a context-relevant remembered media candidate exists, it may replace text with a 28% media chance;
- otherwise the response comes from curated non-offensive meme families: literary, reader, art, engineering, technical, science, absurd and neutral;
- style is selected from recent same-topic conversation context, never from a stored profession/profile of a specific user;
- recent bot greeting replies are avoided when another variant is available;
- normal Generation v3 is not invoked for a recognized greeting, including when the greeting intentionally receives no reply;
- one-token forms such as `споки` and `гн` are evaluated after broad Culture Memory ingestion even though they are below the legacy two-token text-generation threshold.

Greeting action metadata stores only bounded operational fields such as greeting kind/style and safe media diagnostics. It does not persist raw greeting text, raw context or a derived user profile. The outer hard topic denylist remains authoritative: blocked writer-chat topics stop before greeting settings/history reads, learning or replies.

## Rollback

Generation v3 is the default. The previous v2 generator remains available for one-release rollback:

```env
ENTERTAINMENT_GENERATION_ENGINE=v3
```

Set:

```env
ENTERTAINMENT_GENERATION_ENGINE=v2
```

to route the same service call through the legacy local generator without a storage migration. Invalid selector values fail safely back to `v3`.

## Production status and observability

Administrators can inspect generation health in an allowed topic with:

- `/fun_generation_status`
- `/fun_gen_status`

The status command exposes aggregate information only for the current `chat_id + topic_id` scope:

- active engine (`v2` / `v3`);
- live generation attempts, successful outputs and `no-output` count for the current topic since the current process started;
- live engine and generation-mode counts for that topic;
- average accepted candidate count and aggregate rejection reasons;
- a rolling health view for the **last 32 generation attempts** in the same topic;
- recent success/no-output rate, engine/mode counts, average candidates and rejection reasons;
- recent success-rate change in percentage points relative to the topic's lifetime live rate;
- successful generation diagnostics reconstructed from the same topic action history for the last 24 hours;
- the explicit v2 rollback environment setting.

The recent-health window is intentionally descriptive only. It does not apply automatic health thresholds, disable generation, switch engines or emit alerts. This keeps operational visibility separate from policy decisions until real production data justifies a threshold.

Live counters and the rolling 32-attempt window are isolated by `(chat_id, topic_id)` and carried task-locally across concurrent `evaluate_topic` calls, so activity in one chat/topic cannot contaminate another topic's status. The process-local scope cache is LRU-bounded to 256 active scopes. Evicting an inactive scope removes its recent window too, while leaving the global compatibility aggregate unchanged.

`no-output` and the rolling recent-health window are intentionally process-local and reset when the bot process restarts. Existing action records persist successful-generation diagnostics, but a new database table is not introduced just to persist failed attempts or rolling telemetry.

The status command is admin-only. Hard-blocked Entertainment topic scopes return silently before an admin lookup or action-history read.

## Diagnostics and privacy

The production metrics collector stores counters only and never stores generated text, source messages, trigger text, context messages or user identifiers. The rolling recent-health deque stores one counter-only snapshot per attempt, capped at 32 entries per active scope; it does not retain `GenerationResult.text`.

Persisted action aggregation reads only these safe generation fields:

- `generation_engine`;
- `generation_mode`;
- `generation_candidate_count`;
- `generation_score_bucket`;
- `generation_rejections`.

Other metadata fields are ignored by the generation-status aggregator. Malformed diagnostic metadata is ignored rather than coerced into output.

Raw source messages, raw context, raw trigger text, Telegram media identifiers, database DSNs and credentials are not added to Generation v3 diagnostics or live metrics.

## Runtime regression coverage

Production-hardening tests cover:

- bounded synthesis on large snapshots;
- full-snapshot anti-copy protection even for a source excluded from the synthesis working set;
- mixed Russian/English chat language;
- Telegram-style `/commands` and `@usernames`;
- slang/meme phrasing;
- fixed 32–64 candidate-target clamping;
- privacy-safe telemetry aggregation;
- chat/topic isolation of lifetime and recent live status metrics;
- 32-attempt recent-window rollover without truncating lifetime counters;
- recent-window LRU cleanup together with the bounded 256-scope cache;
- privacy-safe recent status rendering with no generated text leakage;
- reusable weighted media selection with a 5-minute exact-repeat guard and soft six-hour diversity penalty;
- production media variety among near-equal contextual candidates;
- morphology-aware media matching for inflected Russian context;
- morning/night greeting detection without false positives on ordinary sentences;
- 40% greeting response gating, 20-minute per-topic cooldown and optional contextual media substitution;
- one-token greeting routing, curated-response anti-repeat and non-offensive corpus checks;
- morphology-aware greeting style selection without individual-user profiling;
- greeting privacy metadata and hard-deny short-circuiting;
- admin-only status behavior and hard-deny short-circuiting.

## Offline quality gate

The repository contains deterministic synthetic/anonymized v2↔v3 evaluation fixtures. They measure:

- exact replay rate;
- unsafe longest-source-overlap rate;
- current TopicAnchor relevance;
- supported n-gram ratio;
- output diversity;
- no-output rate;
- mean and p95 generation latency.

The gate requires v3 to preserve or improve topical relevance and phrase support relative to v2 on the fixture while not increasing replay/unsafe overlap. Tiny single-source corpora are expected to fail closed instead of copying.

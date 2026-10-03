# Entertainment Generation v3 — Chat-Only Language Engine

Generation v3 is the default local text engine for Entertainment. It is designed to synthesize new topic-relevant chat phrases from the same bounded Culture Memory snapshot that Phase C already provides, without loading external text corpora or scanning the full long-term history on every request.

## Source boundary

The engine may use only:

- Culture Memory events from the current `chat_id + topic_id` scope already selected by the Phase C snapshot;
- the bounded recent conversation context from that same scope;
- the exact current trigger text when the action is a direct reply;
- recent outputs of this Entertainment bot for replay suppression.

The engine does **not** load stories, fairy tales, books, Ficbook texts, Lexicon dictionaries, internet text, neighboring forum topics, other chats, external AI APIs, downloaded language models, vector databases or GPU workers.

The writers-chat topic scopes `292358`, `14637` and `42817` remain behind the existing hard Entertainment denylist. They stop before Culture Memory reads and before Generation v3 is invoked.

## Synthesis pipeline

1. Build a `TopicAnchor` from at most the latest 40 context messages.
2. In direct-reply mode, remove exact legacy copies of the trigger from context weighting and add the trigger once as the strongest explicit signal.
3. Normalize Culture messages into distinct source identities so repeated weighting of one source cannot imitate multi-source composition.
4. Build bounded local 5→4→3→2→1 token transition indexes.
5. Generate candidates from:
   - deterministic bounded phrase-chunk bridges across the most topical distinct sources;
   - controlled source crossovers;
   - 5→1 transition backoff.
6. Hard-reject unsafe candidates before final ranking.
7. Rank accepted candidates by current topic fit, direct-trigger coverage, phrase support, source composition, soft morphology and bounded structural signals.
8. Only after text validation succeeds, apply the existing local emoji style layer.

`pymorphy3` is a soft linguistic signal only. Commands, usernames, English tokens and slang remain valid when morphology is unavailable or uncertain. Token analysis is held in a bounded 4096-entry LRU cache.

## Anti-copy invariants

Generation v3 fails closed rather than weakening copy constraints when the corpus is too small.

- exact source replay is rejected;
- exact recent bot-output replay is rejected;
- repeated trigram loops are rejected;
- the longest contiguous overlap with any one source may not exceed 6 words;
- the overlap with any one source may not exceed 70% of candidate words;
- candidates of 6+ words require support from at least two distinct normalized source identities;
- repeated weighted copies of the same source never count as independent sources.

The final direct-reply selection order is intentionally bounded and explicit: maximum supported trigger coverage first, then the strongest phrase-support band, then the strongest topical-relevance band, with controlled variation only inside that safe high-quality set.

## Bounded runtime behavior

Long-term retention may hold roughly 100,000 canonical events per forum topic, but Generation v3 does not scan that entire history. It consumes the existing Phase C bounded snapshot: up to `ENTERTAINMENT_GENERATION_SAMPLE_LIMIT` recent events plus the small deterministic historical windows already used by Culture Memory.

Candidate generation is also bounded:

- target candidate count: 48;
- accepted constructor range: 32–64;
- raw candidate processing cap: 160;
- deterministic structured bridge source cap: 24 topical distinct sources;
- deterministic structured candidate cap: 64.

No schema migration is required for Generation v3.

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

## Diagnostics and privacy

Action metadata may contain only safe generation diagnostics such as:

- engine (`v2` / `v3`);
- mode (`autonomous` / `direct_reply`);
- accepted candidate count;
- score bucket;
- aggregate rejection counts.

Raw source messages, raw context, raw trigger text, Telegram media identifiers, database DSNs and credentials are not added to Generation v3 diagnostic metadata.

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

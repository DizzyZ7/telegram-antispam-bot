# Entertainment Generation v3 — Chat Language Engine

## 1. Goal

Upgrade Entertainment text generation from phrase-aware v2 into a local, context-sensitive language engine that produces more coherent, chat-native replies while preserving the bot's autonomous behavior, topic isolation, PostgreSQL-first persistence, and Bothost-friendly resource profile.

Success means:
- replies feel like they belong to the current conversation, not only to the general chat corpus;
- direct replies anchor to the specific message being answered;
- generated text preserves natural local phrasing while avoiding exact replay;
- grammar and morphology are noticeably less broken than v2;
- no external AI API is required for normal operation;
- performance remains appropriate for one aiogram process on Bothost.

## 2. Non-goals

This phase does not add:
- a heavyweight local transformer/LLM;
- mandatory OpenAI or other external AI provider calls;
- vector databases or embeddings;
- cross-chat memory;
- media/meme generation;
- user-specific psychological profiling.

An optional external rewrite/rerank provider may be added later behind a provider interface, but v3 must be complete without it.

## 3. Current baseline

Generation v2 already provides:
- phrase-preserving bigram continuation;
- one controlled crossover;
- recent-context bias;
- candidate generation and reranking;
- junk suppression;
- no exact replay;
- topic-isolated source corpora.

Its main limitations are:
- direct reply and ordinary autonomous generation share the same text path;
- current context is represented mostly as a bag of recent words;
- there is no lemma-based topical matching;
- morphology is not used in ranking;
- historical messages are not weighted strongly enough by recency and conversational locality;
- there is no benchmark harness comparing generations across engine versions.

## 4. Proposed architecture

New modules:

```text
entertainment/
  generation.py          # public orchestration API and compatibility facade
  language.py            # token/lemma/phrase analysis
  generation_v3.py       # v3 candidate building and scoring
  evaluation.py          # offline quality metrics + benchmark helpers
```

`service.py` remains the runtime integration point. It supplies generation mode and context; generation modules stay independent from Telegram and storage.

## 5. Generation modes

### 5.1 Autonomous phrase

Used for normal spontaneous output and supervisor output.

Inputs:
- long-term topic corpus (bounded by current generation sample limit);
- recent conversation window;
- recent bot outputs for novelty filtering.

The engine should produce a new phrase reflecting the active discussion while retaining vocabulary and idioms from the topic history.

### 5.2 Direct contextual reply

Used when a person replies directly to the bot, and later can be reused for explicit reply-trigger logic.

Additional inputs:
- exact trigger message text;
- a short neighborhood around the trigger message when available;
- current recent conversation window.

The trigger message receives the highest topical weight. A direct reply must not merely produce a generic phrase from the topic corpus.

## 6. Linguistic representation

Use the already installed `pymorphy3` for lightweight Russian morphology.

For each word token derive, when practical:
- lowercase surface form;
- normal form / lemma;
- coarse POS tag;
- grammatical features useful for penalties (case, number, gender where present).

Analysis must be cached in-process with a bounded cache so repeated common words do not repeatedly invoke morphological parsing.

Unknown words, slang, usernames, English words, typos and chat-specific vocabulary must remain usable. Morphology is advisory, never a hard validity gate.

## 7. Phrase model

Build layered continuation indexes over each generation request:

- 5-token context → next token;
- 4-token context → next token;
- 3-token context → next token;
- 2-token context → next token;
- 1-token context as last-resort backoff.

The engine should prefer the longest supported context and back off only when necessary.

In addition, preserve observed phrase chunks of roughly 2–6 word tokens. Candidate construction may cross from one observed phrase to another only when there is a supported lexical/lemma bridge or a safe punctuation/conjunction boundary.

The engine must not reconstruct an entire original source message unchanged.

## 8. Context weighting

Use three scopes with descending weight:

1. **Trigger context** — exact direct-reply message, when present.
2. **Conversation context** — approximately the latest 20–40 suitable messages from the same topic.
3. **Topic history** — the broader generation sample from the same topic.

Weighting should be recency-based rather than binary. Recent examples and transitions receive higher sampling/scoring weight, but old chat culture is still available.

Topic relevance is computed using lemmas plus surface-form overlap so inflected Russian words can still match the same subject.

## 9. Candidate generation

Generate multiple candidates per request (target range 32–64, configurable internally).

Each candidate should:
- start from a weighted phrase fragment relevant to current context;
- continue using longest-context n-gram transitions;
- allow limited controlled phrase crossover;
- stop at a natural sentence boundary or bounded token limit;
- avoid pathological loops and repeated fragments.

Candidate generation must remain deterministic under a supplied seeded `random.Random` for tests.

## 10. Candidate scoring

Rank candidates using a composite score.

Positive signals:
- supported 3/4/5-gram ratio;
- overlap with trigger lemmas (direct reply mode);
- overlap with recent conversation lemmas;
- recency-weighted phrase support;
- natural sentence length;
- ending at an observed/natural boundary;
- use of chat-native vocabulary.

Negative signals:
- exact or near replay of source messages;
- excessive overlap with one single source sentence;
- dangling preposition/conjunction;
- numeric/punctuation fragments;
- repeated n-grams / loops;
- topic drift;
- suspicious morphology transitions.

Morphology penalties should be soft. Examples of likely penalties:
- adjective/noun agreement mismatch when confidence is high;
- obviously incompatible case/number bridges after a crossover;
- broken preposition + case combinations when confidently detected.

Do not reject slang or intentionally ungrammatical chat language simply because `pymorphy3` dislikes it.

## 11. Novelty and anti-copy rules

Keep the existing novelty layer, but add generation-local replay checks:
- reject exact source message replay;
- reject candidates with excessive contiguous overlap with a single source message;
- reject candidates that repeat a recent bot output;
- allow short common local phrases to survive (otherwise chat style is lost).

The engine should remix, not quote.

## 12. Service integration

Change `EntertainmentService._generate_novel_text` to provide explicit generation context.

For autonomous generation:
- pass recent conversation messages separately from long-term corpus.

For direct reply:
- pass the trigger message text;
- select direct-reply generation mode;
- preserve the same autonomy budgets and persisted action history.

No changes to moderation, Lexicon, storage schema, or autonomy budgets are required for this phase.

## 13. Performance constraints

Bothost compatibility is mandatory.

Targets:
- no model downloads;
- no GPU requirement;
- no new background worker;
- no DB schema migration;
- no scanning all 100k stored messages per request;
- generation works only on the existing bounded generation sample;
- morphology cache is bounded;
- target typical generation latency under ~250 ms on a normal small CPU container for a 1,500-message sample, with tests/benchmark reporting actual values rather than enforcing a brittle hard CI limit.

If morphology makes a candidate pass too expensive, analysis should be limited to context vocabulary and candidate words rather than the entire historical corpus.

## 14. Offline benchmark

Add a benchmark/evaluation harness that can compare v2 and v3 on the same corpus and random seeds.

Metrics should include:
- exact replay rate;
- fragment/junk rejection rate;
- supported 3/4-gram ratio;
- context-lemma overlap;
- direct-trigger relevance;
- output diversity across seeds;
- generation latency.

The benchmark should include regression corpora based on observed bad production outputs such as fragmentary transport/travel phrases.

The benchmark is not a claim of human-level language quality. It is a repeatable engineering signal to prevent regressions.

## 15. Testing strategy

### Unit tests
- lemma extraction and caching;
- longest-context backoff;
- phrase bridge selection;
- morphology penalty behavior;
- anti-copy contiguous-overlap logic;
- direct-trigger scoring;
- recency weighting;
- deterministic seeded generation.

### Regression tests
- current v2 bad-output corpus;
- no `"Капец, вот: 3."`-style fragments;
- no `"Электричка скорее О привет!"`-style unsupported transitions;
- direct replies contain meaningful lexical/lemma relation to trigger text often enough under fixed seeds;
- no exact source replay.

### Integration tests
- `EntertainmentService` passes trigger context only for direct replies;
- ordinary autonomous generation remains topic-isolated;
- no changes to persisted action budgets;
- `/fun_generate` uses v3 autonomous mode.

## 16. Rollout

Implement behind an internal engine selector first:

```text
v2 -> existing generator
v3 -> Chat Language Engine
```

Default to v3 after regression and benchmark checks pass. Keep v2 as a temporary fallback for one release cycle, then remove it if production output is stable.

Add structured log fields:
- engine version;
- generation mode (`autonomous` / `direct_reply`);
- candidate count;
- selected score band;
- generation latency;
- rejection reason summary where useful.

Do not log full private chat corpus or database credentials.

## 17. Acceptance criteria

The phase is ready to merge when:
- v3 regression tests pass;
- service integration tests pass;
- PostgreSQL integration job remains green;
- no new failures appear outside the known repository baseline;
- v3 benchmark shows equal-or-better context relevance and phrase support than v2 on the checked corpora without increasing exact replay;
- typical benchmark latency remains reasonable for Bothost;
- direct-reply mode demonstrably uses its trigger text;
- no new mandatory external service or secret is introduced.

# Entertainment Generation v3 — Chat-Only Language Engine

Date: 2026-10-03
Status: conversational design approved; awaiting written-spec review
Base: `main` at `433ecd2a895c22e17dc033d1c13296d2f1eba92e`

## 1. Goal

Generation v3 must make Entertainment produce **new, chat-native phrases that stay on the current subject**, rather than mainly replaying or lightly crossing old messages.

The engine remains fully local and learns only from Culture Memory of the allowed Telegram topic. It must not read stories, fairy tales, literary corpora, Lexicon word sources, the internet, or any cross-topic/cross-chat material.

Success means:
- an output is recognizably about what people are discussing now;
- wording can be new even when no source message contains the whole sentence;
- local slang, commands, other-bot culture and recurring jokes can influence phrasing through the already-approved Culture Memory weights;
- exact/near copying is rarer than v2;
- direct replies relate to the exact triggering message;
- current B+C autonomy/media/privacy behavior remains unchanged.

## 2. Hard source boundaries

Generation input is **chat-only**.

Allowed sources:
- `CultureMemorySnapshot.generation.source_messages` for the current `chat_id + topic_id`;
- recent same-topic conversation context;
- the direct trigger text only when direct-reply mode is selected;
- Phase C weighted command/other-bot material already admitted by Culture Memory.

Forbidden sources:
- static literary/story corpora;
- Ficbook/text files/books/fairy tales;
- Lexicon game dictionaries;
- neighboring Telegram topics;
- other chats;
- the Entertainment bot's own generated messages as training corpus;
- all events from hard-denied writers-chat topics `292358`, `14637`, `42817`.

The hard topic denylist remains enforced by `scoped_service.py`. Integration tests must prove the v3 engine is never invoked for those scopes.

## 3. Runtime architecture

Add focused modules:

```text
entertainment/
  generation.py       # v2 compatibility facade + engine selector
  language.py         # bounded token/lemma/POS analysis
  generation_v3.py    # candidate synthesis and scoring
  evaluation.py       # offline v2/v3 quality benchmark
```

Do not move Telegram/storage logic into generation modules. `culture_service.py` continues to assemble a bounded Culture snapshot; `scoped_service.py` continues to enforce forbidden topics before generation.

No database migration is required.

## 4. Public generation contract

Introduce:

```python
class GenerationMode(str, Enum):
    AUTONOMOUS = "autonomous"
    DIRECT_REPLY = "direct_reply"

@dataclass(frozen=True, slots=True)
class GenerationRequest:
    source_messages: list[str]
    context_messages: list[str]
    trigger_text: str | None
    mode: GenerationMode
    recent_bot_outputs: list[str]

@dataclass(frozen=True, slots=True)
class GenerationResult:
    text: str
    engine: str
    score: float
    candidate_count: int
    rejection_counts: dict[str, int]
```

`generate_chat_text()` remains as v2 compatibility during one release. New service code calls an engine facade returning `GenerationResult | None`.

Default engine after acceptance is `v3`; `v2` remains an internal rollback option for one release.

## 5. Linguistic analysis

Use installed `pymorphy3` as a soft signal only.

For Russian word tokens derive:
- surface form;
- case-folded form;
- lemma;
- coarse POS;
- confident grammatical features useful for soft penalties.

Unknown/slang/English/usernames/commands remain valid. Morphology must never become a hard gate for chat language.

Use an in-process bounded LRU cache, target maximum 4096 analyzed token forms.

To stay fast, do not morphologically parse the entire 100k history or every token of all 1500 weighted source messages. Analyze current context, candidate words and only source terms needed for scoring.

## 6. Topic anchor

Build a `TopicAnchor` from the latest suitable same-topic context (target 20–40 messages) plus trigger text in direct mode.

Signals:
- weighted content lemmas;
- surface terms for slang/commands/names;
- short phrase anchors observed repeatedly in the recent conversation;
- trigger terms with the highest weight in direct mode.

Phase C currently duplicates trigger text inside legacy `context_messages` to bias v2. V3 must de-duplicate that representation when building `TopicAnchor`: the exact `trigger_text` is a separate input and receives its direct-reply weight exactly once.

A candidate with no meaningful lexical/lemma relation to a non-empty TopicAnchor is rejected unless the recent context contains no usable content terms.

This prevents an old unrelated meme from winning merely because it is common in long-term memory.

## 7. Phrase and transition model

For each generation request build bounded indexes from weighted Culture source messages:
- 5-token -> next token;
- 4-token -> next token;
- 3-token -> next token;
- 2-token -> next token;
- 1-token fallback;
- observed phrase chunks of roughly 2–6 word tokens.

Prefer the longest supported continuation and back off only when needed.

Phrase crossover is allowed only at:
- punctuation/conjunction boundaries; or
- a supported surface/lemma bridge.

Crossovers inside an unsupported noun/verb phrase receive a strong penalty or are rejected.

## 8. "Own phrase" synthesis rule

V3 must remix rather than quote.

For normal corpora, a candidate is eligible only when:
- it is not an exact normalized source message;
- it is not a near replay of a recent bot output;
- its longest contiguous word overlap with any one source is bounded (target maximum 6 words and maximum 70% of candidate words, whichever is stricter for that candidate);
- candidates of at least 6 words show composition support from at least two **distinct normalized source-text identities**, or contain a genuinely synthesized supported bridge;
- repeated 3-grams/loops are rejected.

Culture Memory represents source weighting partly by repeating source strings. Those repeated weighted entries **must not** count as separate composition sources. Source identity is based on normalized source text, not list position.

Short common chat phrases are allowed, but an exact remembered sentence is not considered "new" merely because punctuation changed.

If v3 cannot make a safe candidate, return `None`; do not weaken anti-copy rules to force output.

## 9. Candidate generation

Generate a target of 48 candidates per request (allowed internal range 32–64).

Candidate construction:
1. choose a start phrase weighted by TopicAnchor relevance and Culture source weight;
2. continue using 5 -> 1 backoff;
3. allow a small number of controlled bridges/crossovers;
4. stop on an observed sentence boundary or bounded token limit;
5. reject loops/fragments early.

All random choices use supplied `random.Random`, making fixed-seed tests deterministic.

## 10. Candidate scoring

Positive signals:
- trigger lemma/surface overlap in direct mode;
- recent TopicAnchor overlap;
- supported 5/4/3-gram ratio;
- recency/Culture support inherited from weighted `source_messages`;
- support from more than one distinct normalized source path;
- natural length and sentence boundary;
- chat-native vocabulary from current topic.

Negative signals:
- topic drift;
- contiguous copying from one source;
- near-replay of recent bot output;
- unsupported crossover;
- dangling preposition/conjunction;
- punctuation/numeric fragments;
- repeated n-grams;
- high-confidence morphology mismatch.

Topic relevance is stronger than generic historical popularity. In direct mode trigger relevance is the strongest semantic component.

Morphology penalties remain soft so intentional chat grammar/slang survives.

## 11. Service integration

Phase C already provides one bounded `CultureMemorySnapshot` for text/emoji/media.

Generation v3 consumes only `snapshot.generation`:
- autonomous: `source_messages + context_messages`, no trigger;
- direct reply: same inputs plus exact trigger text and `DIRECT_REPLY` mode;
- `/fun_generate`: autonomous mode.

Existing emoji styling runs **after** a v3 text candidate passes novelty/anti-copy validation.

Existing media candidate selection and autonomy budgets are untouched.

Blocked-topic behavior remains silent: no snapshot read and no generation invocation.

## 12. Performance constraints

- no external AI API;
- no model download;
- no GPU;
- no vector DB;
- no new worker;
- no new storage schema;
- never scan full ~100k topic history;
- use only Phase C bounded snapshot input;
- bounded morphology cache;
- target typical generation latency below about 250 ms on a normal small CPU container with a 1500-message input sample;
- benchmark latency is reported, not used as a brittle CI hard failure unless a severe regression threshold is exceeded.

## 13. Benchmark and quality gate

Add deterministic offline evaluation comparing v2 and v3 on the same seeds and chat-like synthetic/anonymized corpora.

Metrics:
- exact replay rate;
- near-copy/longest-source-overlap rate;
- topic-anchor lemma overlap;
- direct-trigger relevance;
- supported 3/4/5-gram ratios;
- output diversity across seeds;
- no-output rate;
- latency.

Regression cases must include:
- transport/travel discussion where unrelated historic words must not drift in;
- `/spawn`/bot-command chat culture;
- short meme exchanges;
- direct replies with an explicit current subject;
- examples analogous to old fragments like `Капец, вот: 3.` and unsupported transitions like `Электричка скорее О привет!`.

Do not commit private raw chat dumps to the repository.

Acceptance requires v3 to be equal or better than v2 on topical relevance and supported phrase structure, with no increase in exact/near replay.

## 14. Testing

Unit tests:
- lemma analysis + bounded cache;
- TopicAnchor construction and trigger de-duplication;
- 5 -> 1 backoff;
- supported bridge selection;
- direct trigger weighting;
- anti-copy longest contiguous overlap;
- weighted duplicate sources do not fake multi-source composition;
- genuine multi-source composition rule;
- soft morphology penalties;
- deterministic seeded generation.

Service tests:
- autonomous/direct modes passed correctly;
- `/fun_generate` uses v3 autonomous mode;
- emoji styling remains post-generation;
- media/autonomy budgets unchanged;
- blocked topics `292358`, `14637`, `42817` never call v3;
- topic isolation remains strict.

CI:
- full unittest discovery must remain green;
- PostgreSQL 17 integration remains green even though v3 requires no DB changes.

## 15. Observability and rollback

Structured safe diagnostics may include:
- engine `v2|v3`;
- mode `autonomous|direct_reply`;
- candidate count;
- selected score bucket;
- latency;
- aggregate rejection reason counts.

Never log source messages, trigger text, captions, `file_id`, DSN or credentials.

Keep an internal/env-selectable v2 fallback for one release. V3 becomes default only after regression + benchmark acceptance.

## 16. Acceptance criteria

Ready for merge when:
- outputs are synthesized rather than exact/near source replays under regression seeds;
- outputs relate to the active topic when usable context exists;
- direct replies measurably use trigger content;
- no story/literary/static corpus is referenced anywhere in v3;
- denied topics cannot reach v3;
- current Culture Memory/emoji/media/privacy/autonomy behavior remains intact;
- benchmark topical relevance and phrase support are >= v2 without higher replay rate;
- full unittest and PostgreSQL jobs are green;
- performance remains suitable for the current small CPU deployment;
- no new external service, secret or persistent data dependency is introduced.

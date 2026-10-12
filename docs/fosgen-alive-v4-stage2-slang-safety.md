# Fosgen Alive v4 — Stage 2: community slang + safe outgoing humor

## Current behavior
Culture Memory retains eligible chat messages by chat/topic with consent, deletion and existing moderation boundaries. Generation v3 already synthesizes from this material, including unfamiliar words. Stage 2 adds a conservative, original slang callback from the same bounded Memory snapshot.

## Learning and evidence
- Only eligible, non-forwarded, non-command, human text in the exact chat/topic is inspected; unsafe human text never becomes a slang source.
- Familiar harmless colloquial expressions require two distinct message events from two authors.
- Novel invented words require explicit introduction as a new word/meme, at least three distinct message events and two authors.
- At least one occurrence must be fresh (within 20 minutes). Historical words cannot revive a dead conversation.
- Evidence is recalculated from existing Culture Memory each time an approved output slot is available, rather than stored as a separate global dictionary.
- The original, vetted Generation v3 response has priority. Slang fallback is attempted only occasionally after a failed primary/literary generation.
- Selected words are used only in short, prewritten non-personal jokes and are checked again before sending.

## Protecting people
- Outgoing-only safety filter checks existing profanity/masked-profanity moderation plus conservative rejection of direct second-person targeting, @mentions and insulting epithets.
- Source-language filter still permits normal human speech (including harmless second-person expressions), but excludes abusive examples from generation.
- Production text generation retries unsafe candidates, and the final service performs another output check after any literary, banter or slang fallback.
- The bot must not address participants with personal put-downs. This conservative approach may discard innocent jokes too.
- There is no OCR or vision moderation for text embedded into pictures/stickers in this version. The filter cannot provide an absolute guarantee against all conceivable novel obfuscations.

## Compatibility
No migrations, new ENV, third-party AI or extra messages. Existing topic isolation, storage opt-out and deletion, settings, Presence limits, Writers/Lexicon boundaries and commands are unchanged. Only a boolean callback marker and the generation engine category are added to the existing diagnostics, never the extracted new word or user ID.

## Tests and rollout
Unit tests verify multi-author learning, novel slang requiring introduction, no spam by a single user, cross-topic boundaries, old/forwarded/command/bot/more abusive text rejection, memory deletion, output deduplication and the stricter reply filter. Runtime regression verifies slang callbacks take an already authorized text slot and an offensive mocked generation candidate never leaves as a bot message.

Run full Python, PostgreSQL and Docker CI. After merging the approved PR, rebuild Bothost from main while retaining DATABASE_URL and the existing PostgreSQL data, and review observed output text/media ratios in the normal chat.

# Meme Word Rewrite Implementation Plan

> For agentic workers: execute natively using superpowers:executing-plans; one fresh whole-branch review before merge.

Goal: remove the unwanted meme, send one configured sticker and return a harmless corrected copy in the same Writers topic.
Architecture: pure word policy + scoped middleware before learning + durable sticker configuration through the existing pool.
Tech Stack: Python, aiogram, existing PostgreSQL configuration, local atomic file fallback only without PostgreSQL.
Spec: docs/superpowers/specs/2026-11-11-meme-word-rewrite-design.md

## Global Constraints

Only existing Writers scope; no Lexicon-only/private expansion. No external AI, added dependencies, real Telegram writes or paid calls. Preserve profanity moderation and historical-memory safeguards. Never repost other unsafe content. No send retry after uncertain delivery.

## Review Focus

- Word boundaries, inflections, case and ordinary similar Russian words.
- Failed deletion must not publish replacements; sticker failure must not suppress the text.
- Admin identity and chat scope are checked before configuration reads/writes.
- Plain text, Unicode, long text, captions and forum topics survive without markup interpretation or invalid deleted-message replies.
- Update replays, edits, unsafe historical fragments and coexistence with profanity do not create a second response or retrain forbidden forms.

### Task 1: Pure policy and Entertainment safety

Files: writers_meme_policy.py; entertainment/moderation_safety.py; tests/test_writers_meme_policy.py; tests/test_entertainment_moderation_safety.py.
Interface: rewrite_meme_words(text: str)->str; contains_meme_words(text: str|None)->bool.
- [x] Write tests for exact forms, inflections, case, repeated words, punctuation and harmless near matches; demonstrate RED.
- [x] Implement policy; block new/historical/output unsafe meme language in Entertainment; verify real service learning/snapshot boundaries GREEN and commit.

### Task 2: Persistent configuration and scoped rewrite

Files: writers_meme_rewrite.py; lexicon_postgres_config.py; writers_moderation.py; main.py; tests/test_writers_meme_rewrite.py; tests/test_lexicon_postgres_config.py; docs/fosgen-meme-rewrite.md.
Interfaces: MemeStickerStore.get(chat_id)->str|None; set(chat_id,file_id)->None; WritersMemeMiddleware(app,scope,store,on_message_deleted); register_writers_meme_handlers(app,scope,store,on_message_deleted).
- [x] Write failing scope/admin/persistence/deletion/delivery/topic/replay/edit/media/long-text tests against actual aiogram messages and handler boundaries.
- [x] Implement storage, middleware, commands and startup wiring. Preserve priority and no other-chat behavior. Verify GREEN and document installation.
- [x] Run full Python/Node, compile/diff checks, one independent review and one necessary RED→GREEN fix pass.

Release gate: publish the verified tree, require all six CI checks including real PostgreSQL, and merge under existing authorization. Deployment and live sticker installation are performed by the chat administrator after rebuilding Bothost.

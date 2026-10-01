# Telegram Anti-Spam Bot

This repository contains the Telegram bot and its scoped moderation, statistics and Lexicon game features.

## Entertainment mode

The entertainment layer is enabled only for explicitly allowed chats. The first enabled chat is `-1002619489118`.

Current v1 supports per-chat learning, local phrase generation, spontaneous replies and admin controls. A larger v2 architecture is being designed specifically for this bot and Bothost rather than copying another bot's terminology or UX.

Design and implementation docs:

- `docs/superpowers/specs/2026-10-01-entertainment-autonomy-v2-design.md`
- `docs/superpowers/plans/2026-10-01-entertainment-foundation-persistence.md`

The first implementation phase moves entertainment to a package, introduces per-topic isolation and adds PostgreSQL-first persistence with SQLite fallback. Later phases add the autonomous activity engine, media memory, local meme rendering, polls/events, our own admin presets and optional AI providers.

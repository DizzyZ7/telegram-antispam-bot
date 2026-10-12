# Fosgen Alive v4 — Stage 1: Text Recovery v2 + Media Variety

## Why

The live Culture Memory pipeline already supports generated text, learned emoji
styling, remembered stickers, photos and animations. However, if the local
Generation v3 could not produce an acceptable sentence, the existing fallback
was limited to four literary themes. Ordinary conversations then selected media
or returned no output. Also, similarly relevant media were ranked largely
without looking at recently sent media types.

## Change

- Keep the existing Generation v3 and its checks as the primary source of text.
- When v3 and the literary fallback are unavailable, the banter recovery module
  may recover one original, short micro-joke grounded in a fresh (<=20 minutes)
  human message from the same chat/topic. Current supported conversational
  motifs: waiting, celebration, technical hiccups, laughter, choice.
- Do not use bot messages, forwards, commands, old context, links, another
  forum topic, participants' private data or personal profiles as a trigger.
  All candidate text and source triggers must pass the existing entertainment
  moderation filter. The 24h anti-repeat check prevents a small line catalog
  from turning into a spam loop; exhaustion means no banter recovery.
- Learned chat emoji styling may be applied to recovered text, under the
  existing emoji-selection logic.
- Previously accepted/approved message budget is unchanged. Text recovery
  happens after all autonomy/presence checks, and still realizes at most one
  message in that slot.
- When already relevant and near-equal remembered media compete, softly
  boost underused types based on up to six successful media actions in the
  same chat/topic. This never bypasses the type-specific relevance thresholds
  or exact-repeat restrictions. Missing legacy metadata disables the boost.

## Telemetry and compatibility

New local recovered text is recorded with generation_engine=banter_v1,
banter_topic=<non-personal category>, output_kind=text. No raw chat context
is added to telemetry beyond the existing recorded output.
Stickers/photos/animations continue recording their existing media_type;
that history guides the new soft variety weight.

No new ENV, migrations, third-party API, external LLM, or changes to existing
Telegram commands. Existing writer-chat blocked topics, Lexicon isolation,
memory consent, deletion and moderation boundaries are unaffected.

## Release / verification

Run full Python unittest suite and PostgreSQL service test jobs in CI.
Regression tests cover fresh/stale contexts, link/forward/bot/command rejection,
topic isolation, original text, anti-repetition, runtime text-before-media,
stale fallback, relevant mixed media and cross-topic media isolation.

Deploy only after CI and PR review: rebuild Bothost container from updated
main, preserving PostgreSQL and ENV. Test spontaneous text in an active
normal topic, emoji/sticker/photo callbacks and all moderation commands.
Observe generation_engine and output_kind counters. It is not expected that
every approved action has a text response or that the bot will speak when
adaptive presence says to stay quiet.

## Deliberately out of scope

Real Telegram message reactions, visual image recognition, external LLM
generation, inferred personal traits and extra unsolicited messages
are not implemented by this stage.

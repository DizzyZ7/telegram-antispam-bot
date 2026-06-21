# Writers chat moderation

The targeted moderation module is enabled only for `@chat_IKF`. It keeps the existing math CAPTCHA and uses the same forum topic for moderation replies.

Runtime environment variables:

- `WRITERS_CHAT_USERNAME=chat_ikf`
- `WRITERS_RULES_URL=https://t.me/chat_IKF/168194/168197`
- `WRITERS_CHAT_ID=-100...` (optional; otherwise resolved at startup)
- `WRITERS_WARNING_COOLDOWN_SECONDS=60`

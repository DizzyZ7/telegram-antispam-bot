"""Startup hook for targeted forum-chat moderation."""

from writers_moderation import register_writers_chat_handlers


def install(module):
    scope = register_writers_chat_handlers(module)
    return scope

"""Main integration note: this module is imported by the production entry point."""

from writers_moderation import register_writers_chat_handlers


def configure(module):
    return register_writers_chat_handlers(module)

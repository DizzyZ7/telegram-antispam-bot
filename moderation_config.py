import os

WRITERS_CHAT_ID = -1002619489118


def get_writers_rules_url() -> str:
    return os.getenv("WRITERS_RULES_URL", "")

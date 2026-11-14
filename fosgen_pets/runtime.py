"""Optional Pets lifecycle: an unavailable pet service never disables moderation."""
from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from pathlib import Path
from typing import Any, Mapping

from .postgres import PostgresPetStore
from .telegram import register_pet_handlers

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class PetsConfig:
    enabled: bool = False
    chat_ids: frozenset[int] = frozenset()

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> PetsConfig:
        env = os.environ if environ is None else environ
        if env.get("FOSGEN_PETS_ENABLED", "0") != "1":
            return cls()
        raw = env.get("FOSGEN_PETS_CHAT_IDS", "")
        values = [value.strip() for value in raw.split(",") if value.strip()]
        if not values or len(values) > 100:
            raise ValueError("Explicit participating group IDs required")
        if any(len(value) > 20 or not value.startswith("-") or not value[1:].isascii()
               or not value[1:].isdigit() for value in values):
            raise ValueError("Participating chat IDs must be negative integers")
        ids = frozenset(map(int, values))
        if any(not -(2**63) < chat_id < 0 for chat_id in ids):
            raise ValueError("Participating chat ID out of range")
        return cls(True, ids)


@dataclass
class PetRuntime:
    store: PostgresPetStore
    service: Any

    async def stop(self) -> None:
        try:
            await self.store.close()
        except Exception as exc:
            LOGGER.warning("FOSGEN_PETS_CLOSE_FAILED error_type=%s", type(exc).__name__)


async def start_pet_runtime(
    app: Any, *, database_url: str | None,
    excluded_chat_ids: frozenset[int] = frozenset(),
    environ: Mapping[str, str] | None = None,
) -> PetRuntime | None:
    try:
        config = PetsConfig.from_env(environ)
    except ValueError:
        LOGGER.warning("FOSGEN_PETS_DISABLED reason=invalid_config")
        return None
    if not config.enabled:
        return None
    if not database_url or not database_url.strip():
        LOGGER.warning("FOSGEN_PETS_DISABLED reason=database_url_missing")
        return None
    effective_ids = config.chat_ids.intersection(getattr(app, "ALLOWED_CHATS", ())).difference(excluded_chat_ids)
    if not effective_ids:
        LOGGER.warning("FOSGEN_PETS_DISABLED reason=no_participating_allowed_chats")
        return None
    store = None
    try:
        store = PostgresPetStore(database_url)
        await store.initialize()
        service = register_pet_handlers(app, store, chat_ids=frozenset(effective_ids))
    except Exception as exc:
        # Asyncpg exceptions may contain connection details: log only the class.
        LOGGER.warning("FOSGEN_PETS_DISABLED reason=startup_failed error_type=%s", type(exc).__name__)
        try:
            if store is not None:
                await store.close()
        except Exception as close_exc:
            LOGGER.warning("FOSGEN_PETS_CLOSE_FAILED error_type=%s", type(close_exc).__name__)
        return None
    LOGGER.info("FOSGEN_PETS_READY backend=postgres groups=%s unlock_messages=100", len(effective_ids))
    return PetRuntime(store, service)


def register_pettest_runtime(
    app: Any, *, data_dir: Path, environ: Mapping[str, str] | None = None,
) -> bool:
    env = os.environ if environ is None else environ
    if env.get("FOSGEN_PETTEST_ENABLED", "0") != "1":
        return False
    from .pettest import parse_admin_ids, register_pettest_handlers
    try:
        admin_ids = parse_admin_ids(env.get("FOSGEN_PETTEST_ADMIN_IDS", ""))
        if not admin_ids:
            raise ValueError("No laboratory administrators configured")
        register_pettest_handlers(app, admin_ids=admin_ids, data_dir=data_dir)
    except Exception as exc:
        LOGGER.warning("FOSGEN_PETTEST_DISABLED reason=startup_failed error_type=%s", type(exc).__name__)
        return False
    LOGGER.info("FOSGEN_PETTEST_READY admins=%s scope=private", len(admin_ids))
    return True

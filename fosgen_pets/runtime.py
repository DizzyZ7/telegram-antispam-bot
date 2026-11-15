"""Optional Pets lifecycle: an unavailable pet service never disables moderation."""
from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from pathlib import Path
from typing import Any, Mapping

from .postgres import PostgresPetStore
from .telegram import register_pet_handlers, register_pet_status_handlers

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class PetsConfig:
    enabled: bool = False
    chat_ids: frozenset[int] = frozenset()
    new_asset_version: int = 2
    v3_art_approved: bool = False

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
        version = env.get("FOSGEN_PETS_NEW_ASSET_VERSION", "2")
        approval = env.get("FOSGEN_PETS_V3_ART_APPROVED", "0")
        if version not in ("2", "3") or approval not in ("0", "1"):
            raise ValueError("Invalid pet art issuance configuration")
        if version == "3" and approval != "1":
            raise ValueError("Version 3 production art requires explicit approval")
        return cls(True, ids, int(version), approval == "1")


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
    trace: Any = None,
    isolated_scope: Any = None,
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
    isolated_ids = frozenset()
    normal_ids = config.chat_ids.intersection(getattr(app, "ALLOWED_CHATS", ())).difference(excluded_chat_ids)
    if isolated_scope is not None:
        from lexicon_game_scope import LexiconGuardScope
        if not isinstance(isolated_scope, LexiconGuardScope) or not isolated_scope.registered_in(getattr(app, "dp", None)):
            LOGGER.warning("FOSGEN_PETS_DISABLED reason=isolated_guard_not_registered")
            return None
        normal_ids = normal_ids.difference(isolated_scope.chat_ids)
        isolated_ids = config.chat_ids.intersection(isolated_scope.chat_ids).difference(excluded_chat_ids)
    effective_ids = normal_ids.union(isolated_ids)
    if not effective_ids:
        LOGGER.warning("FOSGEN_PETS_DISABLED reason=no_participating_allowed_chats")
        return None
    store = None
    try:
        store = (PostgresPetStore(database_url) if config.new_asset_version == 2 else
                 PostgresPetStore(database_url, asset_version=config.new_asset_version))
        await store.initialize()
        isolated_options = ({"isolated_chat_ids": frozenset(isolated_ids),
                             "isolated_handler": isolated_scope.handler} if isolated_ids else {})
        service = register_pet_handlers(app, store, chat_ids=frozenset(effective_ids), **isolated_options)
        if trace is not None:
            service.counting.trace = trace
    except Exception as exc:
        # Asyncpg exceptions may contain connection details: log only the class.
        LOGGER.warning("FOSGEN_PETS_DISABLED reason=startup_failed error_type=%s", type(exc).__name__)
        try:
            if store is not None:
                await store.close()
        except Exception as close_exc:
            LOGGER.warning("FOSGEN_PETS_CLOSE_FAILED error_type=%s", type(close_exc).__name__)
        return None
    # Reuse the explicit operator allowlist; never expose group scope to regular
    # users. A broken diagnostic setting must not disable the actual pet service.
    env = os.environ if environ is None else environ
    if env.get("FOSGEN_PETTEST_ADMIN_IDS", "").strip():
        try:
            from .pettest import parse_admin_ids
            admins = parse_admin_ids(env["FOSGEN_PETTEST_ADMIN_IDS"])
            if trace is not None:
                trace.configure(admins)
            register_pet_status_handlers(
                app, store, admin_ids=admins, requested_chat_ids=config.chat_ids,
                chat_ids=frozenset(effective_ids), asset_version=config.new_asset_version, trace=trace,
                isolated_chat_ids=frozenset(isolated_ids),
            )
        except Exception as exc:
            LOGGER.warning("FOSGEN_PETS_STATUS_DISABLED reason=%s", type(exc).__name__)
    LOGGER.info("FOSGEN_PETS_READY backend=postgres groups=%s chat_ids=%s asset_version=%s isolated_groups=%s unlock_messages=100",
                len(effective_ids), ",".join(map(str, sorted(effective_ids))), config.new_asset_version, len(isolated_ids))
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

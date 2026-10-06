from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .config import TRADER_XER_CHAT_ID, ZeroTrustConfig

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class StartupDiagnostics:
    info: str
    warning: str | None


@dataclass(frozen=True, slots=True)
class LegacyCaptchaDisableResult:
    removed_chat_member_handlers: int
    removed_callback_handlers: int


def require_database_url(config: ZeroTrustConfig, database_url: str | None) -> str | None:
    if not config.chat_ids:
        return None
    value = (database_url or "").strip()
    if not value or not value.lower().startswith(("postgresql://", "postgres://")):
        raise RuntimeError(
            "Zero Trust is enabled but DATABASE_URL is missing or is not PostgreSQL"
        )
    return value


def _format_ids(values: object) -> str:
    normalized = sorted({int(value) for value in values})
    return ",".join(str(value) for value in normalized) if normalized else "<none>"


def build_startup_diagnostics(
    config: ZeroTrustConfig,
    allowed_chats: object,
) -> StartupDiagnostics:
    info = (
        "ZERO_TRUST_SECURITY_SCOPE "
        f"ZERO_TRUST_CHAT_IDS={_format_ids(config.chat_ids)} "
        f"ALLOWED_CHATS={_format_ids(allowed_chats)} "
        f"TTL_SECONDS={config.challenge_ttl_seconds}"
    )
    warning = None
    if config.chat_ids and TRADER_XER_CHAT_ID not in config.chat_ids:
        warning = (
            "ZERO_TRUST_SECURITY_WARNING "
            f"trader_xer_chat_missing={TRADER_XER_CHAT_ID}"
        )
    return StartupDiagnostics(info=info, warning=warning)


def log_startup_diagnostics(
    config: ZeroTrustConfig,
    allowed_chats: object,
    *,
    logger: logging.Logger = LOGGER,
) -> StartupDiagnostics:
    diagnostics = build_startup_diagnostics(config, allowed_chats)
    logger.info(diagnostics.info)
    if diagnostics.warning is not None:
        logger.warning(diagnostics.warning)
    return diagnostics


def _remove_handlers_by_name(observer: Any, names: frozenset[str]) -> int:
    before = len(observer.handlers)
    observer.handlers[:] = [
        item
        for item in observer.handlers
        if getattr(item.callback, "__name__", "") not in names
    ]
    return before - len(observer.handlers)


def disable_legacy_captcha_ownership(module: Any) -> LegacyCaptchaDisableResult:
    """Detach process-local legacy captcha flow before Zero Trust v2 is registered."""
    removed_members = _remove_handlers_by_name(
        module.dp.chat_member,
        frozenset({"on_user_join"}),
    )
    removed_callbacks = _remove_handlers_by_name(
        module.dp.callback_query,
        frozenset({"captcha_handler"}),
    )

    for name, empty_value in (
        ("pending_users", {}),
        ("passed_users", set()),
        ("failed_users", set()),
    ):
        current = getattr(module, name, None)
        if hasattr(current, "clear"):
            current.clear()
        else:
            setattr(module, name, empty_value)

    async def no_legacy_pending_gate(message: object) -> bool:
        return False

    # collect_text_messages / sticker handlers resolve this name through the
    # module globals at call time, so replacing it disables the old RAM gate.
    module.handle_pending_user_message = no_legacy_pending_gate

    return LegacyCaptchaDisableResult(
        removed_chat_member_handlers=removed_members,
        removed_callback_handlers=removed_callbacks,
    )

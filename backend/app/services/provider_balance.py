"""
Provider balance check and Hard Stop enforcement service.

Responsibilities:
  - check_provider_status(provider_id) — load provider status (normal/warning/hard_stop),
    using Redis cache (TTL 30s). If hard_stop, expose fallback_provider_id so the gateway
    can rewrite the request, or signal that no fallback is available (caller returns 503).
  - deduct_provider_balance(provider_id, amount, request_id) — atomically subtract
    base cost from providers.balance_usd and write a provider_balance_log row.
  - check_thresholds(provider) — after each deduction, compare the new balance to
    warning_threshold and hard_stop_threshold. Triggers alerts via a pluggable alert
    handler; task 28/29 will plug in real Resend email dispatch.

Requirement 5: AC2, AC4, AC5, AC6, AC7
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Awaitable, Callable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging_config import get_logger
from app.models.provider import Provider
from app.models.usage import ProviderBalanceLog
from app.redis_client import get_redis

logger = get_logger(__name__)


# ── Constants ─────────────────────────────────────────────────────────────────

REDIS_PROVIDER_STATUS_PREFIX = "provider_status:"
REDIS_PROVIDER_STATUS_TTL = 30  # seconds

# Throttle warning emails to at most once per hour per provider.
WARNING_ALERT_COOLDOWN = timedelta(hours=1)

# Provider status enum values (mirrors the providers.status column)
STATUS_NORMAL = "normal"
STATUS_WARNING = "warning"
STATUS_HARD_STOP = "hard_stop"


# ── Data Classes ──────────────────────────────────────────────────────────────


@dataclass
class ProviderStatus:
    """Snapshot of a provider's operational state for routing decisions."""

    provider_id: uuid.UUID
    name: str
    status: str  # 'normal' | 'warning' | 'hard_stop'
    balance_usd: Decimal
    warning_threshold: Decimal
    hard_stop_threshold: Decimal
    fallback_provider_id: Optional[uuid.UUID]
    is_active: bool

    @property
    def is_hard_stop(self) -> bool:
        return self.status == STATUS_HARD_STOP

    @property
    def has_fallback(self) -> bool:
        return self.fallback_provider_id is not None


@dataclass
class ProviderRoutingDecision:
    """
    Result of routing a request through the provider/fallback chain.

    The gateway uses this to decide whether to forward the call, rewrite the
    request to a fallback provider, or fail with HTTP 503.

    Fields:
        provider: The provider the request should be sent to. None when no
                  available provider exists in the chain (caller returns 503).
        is_fallback: True if the chosen provider is a fallback (i.e. the
                     originally requested provider was hard-stopped).
        reason: Machine-readable reason for the decision: 'ok', 'fallback',
                'hard_stop_no_fallback', 'unknown_provider', 'inactive'.
        original_provider_id: Provider id originally requested (for logging).
    """

    provider: Optional[ProviderStatus]
    is_fallback: bool
    reason: str
    original_provider_id: uuid.UUID

    @property
    def should_block(self) -> bool:
        return self.provider is None


# ── Alert hook ────────────────────────────────────────────────────────────────
#
# Task 28/29 will register a real handler that sends emails via Resend.
# Until then we emit a structured log so the threshold logic is fully testable
# without coupling to the email service.

AlertEvent = str  # 'warning' | 'hard_stop'

AlertHandler = Callable[[Provider, AlertEvent, Decimal], Awaitable[None]]

_alert_handler: Optional[AlertHandler] = None


def register_alert_handler(handler: AlertHandler) -> None:
    """
    Register an async callable invoked on warning / hard_stop alerts.

    Signature: handler(provider, event, balance_after) -> None
    Task 28 wires this to services.email.send_provider_warning / send_provider_hard_stop.
    """
    global _alert_handler
    _alert_handler = handler


async def _emit_alert(provider: Provider, event: AlertEvent, balance_after: Decimal) -> None:
    """Invoke the registered alert handler, logging the event regardless.

    Note: structlog reserves the ``event`` kwarg as the log message, so we pass
    the alert kind under ``alert_event``.
    """
    logger.warning(
        "provider_alert",
        provider_id=str(provider.id),
        provider_name=provider.name,
        alert_event=event,
        balance_usd=str(balance_after),
        warning_threshold=str(provider.warning_threshold),
        hard_stop_threshold=str(provider.hard_stop_threshold),
    )
    if _alert_handler is not None:
        try:
            await _alert_handler(provider, event, balance_after)
        except Exception as exc:  # pragma: no cover — defensive
            logger.error(
                "provider_alert_handler_failed",
                provider_id=str(provider.id),
                alert_event=event,
                error=str(exc),
            )


# ── Public API ────────────────────────────────────────────────────────────────


async def check_provider_status(
    provider_id: uuid.UUID,
    db: AsyncSession,
) -> Optional[ProviderStatus]:
    """
    Return the cached or freshly loaded ProviderStatus for `provider_id`.

    The status is cached in Redis under `provider_status:{provider_id}` with
    TTL 30s. On any state change (hard stop activation, manual release,
    threshold update, balance update) the cache key MUST be invalidated via
    `invalidate_provider_status_cache()` so callers see fresh state immediately.

    Returns None if the provider does not exist.
    """
    cached = await _get_cached_status(provider_id)
    if cached is not None:
        logger.debug(
            "provider_status_cache_hit",
            provider_id=str(provider_id),
            status=cached.status,
        )
        return cached

    provider = await _load_provider(provider_id, db)
    if provider is None:
        return None

    status = _provider_to_status(provider)
    await _cache_status(status)

    logger.info(
        "provider_status_resolved",
        provider_id=str(provider_id),
        status=status.status,
        balance_usd=str(status.balance_usd),
        has_fallback=status.has_fallback,
    )
    return status


async def resolve_provider_for_request(
    provider_id: uuid.UUID,
    db: AsyncSession,
    *,
    max_fallback_depth: int = 3,
) -> ProviderRoutingDecision:
    """
    Resolve which provider should handle a request, walking the fallback chain
    when the requested provider is in hard_stop.

    Routing rules (Requirement 5 AC6, AC7):
      - If the requested provider is unknown → reason='unknown_provider', block.
      - If the requested provider is inactive → reason='inactive', block.
      - If status != 'hard_stop' → return it directly with reason='ok'.
      - If status == 'hard_stop' and a fallback exists → recurse into the
        fallback (up to ``max_fallback_depth`` hops to avoid cycles); if a
        usable fallback is found return it with reason='fallback' and
        is_fallback=True.
      - If status == 'hard_stop' and no usable fallback exists →
        reason='hard_stop_no_fallback', block (caller returns HTTP 503).

    The caller is the gateway endpoint, which translates ``should_block`` into
    an HTTP 503 response.
    """
    visited: set[uuid.UUID] = set()
    current_id = provider_id
    is_fallback = False

    for _ in range(max_fallback_depth + 1):
        if current_id in visited:
            # Cycle in fallback configuration — refuse to loop.
            logger.error(
                "provider_fallback_cycle_detected",
                provider_id=str(current_id),
                original_provider_id=str(provider_id),
            )
            return ProviderRoutingDecision(
                provider=None,
                is_fallback=is_fallback,
                reason="hard_stop_no_fallback",
                original_provider_id=provider_id,
            )
        visited.add(current_id)

        status = await check_provider_status(current_id, db)
        if status is None:
            return ProviderRoutingDecision(
                provider=None,
                is_fallback=is_fallback,
                reason="unknown_provider",
                original_provider_id=provider_id,
            )
        if not status.is_active:
            # Inactive provider behaves like a hard-stop for routing — try fallback.
            if status.fallback_provider_id is not None:
                current_id = status.fallback_provider_id
                is_fallback = True
                continue
            return ProviderRoutingDecision(
                provider=None,
                is_fallback=is_fallback,
                reason="inactive",
                original_provider_id=provider_id,
            )
        if not status.is_hard_stop:
            return ProviderRoutingDecision(
                provider=status,
                is_fallback=is_fallback,
                reason="fallback" if is_fallback else "ok",
                original_provider_id=provider_id,
            )
        # Hard stop — try fallback if configured.
        if status.fallback_provider_id is None:
            return ProviderRoutingDecision(
                provider=None,
                is_fallback=is_fallback,
                reason="hard_stop_no_fallback",
                original_provider_id=provider_id,
            )

        logger.info(
            "provider_hard_stop_routing_to_fallback",
            from_provider_id=str(current_id),
            to_provider_id=str(status.fallback_provider_id),
            original_provider_id=str(provider_id),
        )
        current_id = status.fallback_provider_id
        is_fallback = True

    # Exceeded depth without finding a usable provider.
    logger.error(
        "provider_fallback_depth_exceeded",
        original_provider_id=str(provider_id),
        max_depth=max_fallback_depth,
    )
    return ProviderRoutingDecision(
        provider=None,
        is_fallback=is_fallback,
        reason="hard_stop_no_fallback",
        original_provider_id=provider_id,
    )


async def deduct_provider_balance(
    provider_id: uuid.UUID,
    amount: Decimal,
    request_id: str,
    db: AsyncSession,
    usage_record_id: Optional[uuid.UUID] = None,
    note: Optional[str] = None,
) -> Provider:
    """
    Atomically deduct `amount` (USD, base cost — no markup) from the provider
    balance and write a provider_balance_log row of type `usage_deduct`.

    Caller is responsible for committing the transaction (FastAPI's get_db
    dependency commits at request end). The provider status cache is
    invalidated so subsequent reads see the new balance.

    Args:
        provider_id: Target provider.
        amount: Positive USD amount to deduct (raises ValueError if negative).
        request_id: Originating request id, used in the log note.
        db: Async session.
        usage_record_id: Optional FK back to usage_records.id.
        note: Optional human-readable note.

    Returns:
        The updated Provider ORM instance.

    Raises:
        ValueError: If amount is negative or provider not found.
    """
    if amount < 0:
        raise ValueError("amount must be non-negative")

    stmt = (
        select(Provider)
        .where(Provider.id == provider_id)
        .with_for_update()
    )
    result = await db.execute(stmt)
    provider = result.scalar_one_or_none()
    if provider is None:
        raise ValueError(f"Provider not found: {provider_id}")

    balance_before = provider.balance_usd
    balance_after = balance_before - amount
    provider.balance_usd = balance_after

    log_note = note or f"usage_deduct request_id={request_id}"
    log_entry = ProviderBalanceLog(
        provider_id=provider.id,
        change_type="usage_deduct",
        amount_usd=-amount,  # negative = deduct
        balance_before=balance_before,
        balance_after=balance_after,
        usage_record_id=usage_record_id,
        note=log_note,
    )
    db.add(log_entry)
    await db.flush()

    # Status cache is now stale — drop it so the next request sees fresh balance.
    await invalidate_provider_status_cache(provider.id)

    logger.info(
        "provider_balance_deducted",
        provider_id=str(provider.id),
        provider_name=provider.name,
        amount_usd=str(amount),
        balance_before=str(balance_before),
        balance_after=str(balance_after),
        request_id=request_id,
    )
    return provider


async def check_thresholds(
    provider: Provider,
    db: AsyncSession,
) -> str:
    """
    Compare the provider's current balance to its warning and hard_stop
    thresholds, mutate `providers.status` if needed, and emit alerts.

    Rules (Requirement 5 AC4–AC8):
      - balance < hard_stop_threshold → status=hard_stop, hard_stop_activated_at=now,
        invalidate cache, emit hard_stop alert immediately.
      - balance < warning_threshold (and >= hard_stop_threshold) → status=warning;
        emit warning alert at most once per hour per provider.
      - balance >= warning_threshold → status=normal (auto-recovery is left to the
        manual `release-hard-stop` flow for hard_stop, but warning auto-clears).

    Args:
        provider: The Provider row (already loaded). Caller must keep it in
                  the same session as `db`.
        db: Async session.

    Returns:
        The new status value ('normal' | 'warning' | 'hard_stop').
    """
    balance = provider.balance_usd
    new_status = provider.status
    cache_dirty = False

    # 1. Hard Stop has highest priority.
    if balance < provider.hard_stop_threshold:
        if provider.status != STATUS_HARD_STOP:
            provider.status = STATUS_HARD_STOP
            provider.hard_stop_activated_at = datetime.now(timezone.utc)
            await db.flush()
            cache_dirty = True
            await _emit_alert(provider, "hard_stop", balance)
        new_status = STATUS_HARD_STOP

    # 2. Warning band: below warning, above hard_stop.
    elif balance < provider.warning_threshold:
        if provider.status != STATUS_WARNING:
            provider.status = STATUS_WARNING
            await db.flush()
            cache_dirty = True

        # Throttle warning emails to 1/hour per provider.
        if _should_send_warning(provider):
            provider.last_warning_alert_at = datetime.now(timezone.utc)
            await db.flush()
            await _emit_alert(provider, "warning", balance)
        new_status = STATUS_WARNING

    # 3. Healthy band — clear warning automatically. Hard stop must be cleared
    #    manually by an admin (Requirement 5 AC8), so we do NOT auto-clear it here.
    else:
        if provider.status == STATUS_WARNING:
            provider.status = STATUS_NORMAL
            await db.flush()
            cache_dirty = True
        new_status = provider.status  # may still be hard_stop if admin hasn't released

    if cache_dirty:
        await invalidate_provider_status_cache(provider.id)

    return new_status


async def invalidate_provider_status_cache(provider_id: uuid.UUID) -> None:
    """Drop the cached ProviderStatus for `provider_id`. Non-fatal on Redis errors."""
    try:
        redis = get_redis()
        await redis.delete(_status_cache_key(provider_id))
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning(
            "provider_status_cache_invalidation_failed",
            provider_id=str(provider_id),
            error=str(exc),
        )


# ── Internal Helpers ──────────────────────────────────────────────────────────


def _status_cache_key(provider_id: uuid.UUID) -> str:
    return f"{REDIS_PROVIDER_STATUS_PREFIX}{provider_id}"


def _provider_to_status(provider: Provider) -> ProviderStatus:
    return ProviderStatus(
        provider_id=provider.id,
        name=provider.name,
        status=provider.status,
        balance_usd=Decimal(provider.balance_usd),
        warning_threshold=Decimal(provider.warning_threshold),
        hard_stop_threshold=Decimal(provider.hard_stop_threshold),
        fallback_provider_id=provider.fallback_provider_id,
        is_active=provider.is_active,
    )


async def _load_provider(provider_id: uuid.UUID, db: AsyncSession) -> Optional[Provider]:
    result = await db.execute(select(Provider).where(Provider.id == provider_id))
    return result.scalar_one_or_none()


async def _get_cached_status(provider_id: uuid.UUID) -> Optional[ProviderStatus]:
    try:
        redis = get_redis()
        raw = await redis.get(_status_cache_key(provider_id))
        if raw is None:
            return None
        data = json.loads(raw)
        return ProviderStatus(
            provider_id=uuid.UUID(data["provider_id"]),
            name=data["name"],
            status=data["status"],
            balance_usd=Decimal(data["balance_usd"]),
            warning_threshold=Decimal(data["warning_threshold"]),
            hard_stop_threshold=Decimal(data["hard_stop_threshold"]),
            fallback_provider_id=(
                uuid.UUID(data["fallback_provider_id"])
                if data.get("fallback_provider_id")
                else None
            ),
            is_active=bool(data["is_active"]),
        )
    except Exception as exc:
        logger.warning(
            "provider_status_cache_read_failed",
            provider_id=str(provider_id),
            error=str(exc),
        )
        return None


async def _cache_status(status: ProviderStatus) -> None:
    try:
        redis = get_redis()
        payload = json.dumps(
            {
                "provider_id": str(status.provider_id),
                "name": status.name,
                "status": status.status,
                "balance_usd": str(status.balance_usd),
                "warning_threshold": str(status.warning_threshold),
                "hard_stop_threshold": str(status.hard_stop_threshold),
                "fallback_provider_id": (
                    str(status.fallback_provider_id)
                    if status.fallback_provider_id
                    else None
                ),
                "is_active": status.is_active,
            }
        )
        await redis.set(
            _status_cache_key(status.provider_id),
            payload,
            ex=REDIS_PROVIDER_STATUS_TTL,
        )
    except Exception as exc:
        logger.warning(
            "provider_status_cache_write_failed",
            provider_id=str(status.provider_id),
            error=str(exc),
        )


def _should_send_warning(provider: Provider) -> bool:
    """True if no warning has been sent for this provider in the last hour."""
    last = provider.last_warning_alert_at
    if last is None:
        return True
    # Normalise naive timestamps just in case.
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - last >= WARNING_ALERT_COOLDOWN

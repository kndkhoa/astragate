"""
Post-processing background task for the gateway pipeline.

Runs as a FastAPI BackgroundTask AFTER the response (or stream) has been
delivered to the customer. Responsibilities:

  1. Extract actual token counts from the LiteLLM response.
  2. Detect cache hits — on a cache hit, billed_amount and base_cost_usd
     are zero, the credit hold is released without charging, and the
     provider balance is NOT deducted (Requirement 7 AC2, AC3).
  3. Compute base_cost_usd and billed_amount_usd from model pricing.
  4. Settle credit with the actual billed amount (Requirement 3 AC4, AC5).
  5. Deduct provider balance by base_cost_usd (Requirement 5 AC2).
  6. Insert a usage_records row capturing the full request (Requirement 12 AC1).
  7. Update virtual_keys counters (total_requests, total_tokens, last_used_at).
  8. Run check_thresholds() so balance alerts fire when needed.

Because this runs after the response has already been sent, every failure is
caught and logged — we MUST NOT propagate exceptions back into FastAPI's
background-task runner (which would otherwise crash silently).

The function accepts an optional ``session_factory`` so unit tests can inject
an in-memory session manager. In production it falls back to the global
AsyncSessionLocal.

Requirement 1 AC3, Requirement 3 AC4 AC5, Requirement 5 AC2, Requirement 12 AC1
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Optional

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging_config import get_logger
from app.models.model import Model
from app.models.provider import Provider
from app.models.usage import UsageRecord
from app.models.virtual_key import VirtualKey
from app.services.credit import release_hold, settle_credit
from app.services.provider_balance import (
    check_thresholds,
    deduct_provider_balance,
)

logger = get_logger(__name__)


# A factory is anything callable that returns an async context manager
# yielding an AsyncSession (e.g. ``async_sessionmaker`` instances).
SessionFactory = Callable[[], Any]


# ── Helpers ───────────────────────────────────────────────────────────────────


def _extract_usage(response: dict) -> tuple[int, int, int]:
    """Pull ``prompt_tokens`` / ``completion_tokens`` / ``total_tokens`` from
    a LiteLLM (OpenAI-compatible) response. Missing fields default to 0."""
    usage = response.get("usage") or {}
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    total_tokens = int(
        usage.get("total_tokens") or (prompt_tokens + completion_tokens)
    )
    return prompt_tokens, completion_tokens, total_tokens


def _detect_cache_hit(response: dict) -> bool:
    """
    Detect whether LiteLLM served this response from the exact cache.

    LiteLLM exposes the flag in a few places depending on version; we check
    the documented top-level ``cache_hit`` key first, then fall back to
    ``_hidden_params.cache_hit`` which the router sometimes uses.
    """
    if response.get("cache_hit") is True:
        return True
    hidden = response.get("_hidden_params")
    if isinstance(hidden, dict) and hidden.get("cache_hit") is True:
        return True
    return False


def _compute_base_cost(
    prompt_tokens: int,
    completion_tokens: int,
    input_price_per_1m: Decimal,
    output_price_per_1m: Decimal,
) -> Decimal:
    """
    Compute the provider-side cost (no markup) for a request.

    Prices are quoted per 1,000,000 tokens, so we divide token counts by 1M
    before multiplying. Returned as a Decimal to avoid float drift.
    """
    million = Decimal("1000000")
    input_cost = (Decimal(prompt_tokens) / million) * Decimal(input_price_per_1m)
    output_cost = (Decimal(completion_tokens) / million) * Decimal(output_price_per_1m)
    return input_cost + output_cost


def _resolve_model_name(response: dict, fallback_model: Model) -> str:
    """Use the ``model`` field LiteLLM echoes back (which reflects fallback
    selection); fall back to the configured model_id when absent."""
    candidate = response.get("model")
    if isinstance(candidate, str) and candidate:
        return candidate
    return fallback_model.model_id


def _compute_latency_ms(start_time: float) -> int:
    """
    Convert ``start_time`` (a ``time.monotonic()`` value captured before the
    LiteLLM call) into elapsed milliseconds. Clamped at 0 so clock skew
    cannot produce negative latencies.
    """
    elapsed = max(0.0, time.monotonic() - start_time)
    return int(elapsed * 1000)


# ── Public API ────────────────────────────────────────────────────────────────


async def post_process_usage(
    request_id: str,
    virtual_key_id: uuid.UUID,
    user_id: uuid.UUID,
    model_id: uuid.UUID,
    provider_id: uuid.UUID,
    litellm_response: dict,
    markup_rate: float,
    start_time: float,
    *,
    is_fallback: bool = False,
    session_factory: Optional[SessionFactory] = None,
) -> None:
    """
    Persist usage, settle credit, and deduct provider balance for one request.

    Designed to run as a FastAPI BackgroundTask after the customer has already
    received their response. All exceptions are caught and logged — we never
    re-raise into the background runner.

    Args:
        request_id: The request identifier used during the credit hold.
        virtual_key_id: The Virtual Key that authenticated the request.
        user_id: The owning user.
        model_id: The configured model (database PK), used to look up pricing.
        provider_id: The provider that actually served the request (after any
                     fallback rewrite).
        litellm_response: The parsed JSON response body from LiteLLM.
        markup_rate: The effective markup (resolved before the call).
        start_time: ``time.monotonic()`` snapshot taken just before the
                    LiteLLM call, used to compute latency.
        is_fallback: True if the originally requested provider was hard-stopped
                     and routing fell over to ``provider_id``.
        session_factory: Optional override that returns an async context
                         manager yielding an AsyncSession. Defaults to the
                         global AsyncSessionLocal — tests inject a mock.
    """
    factory: SessionFactory
    if session_factory is not None:
        factory = session_factory
    else:
        # Lazy import — keeps the module importable in unit tests that don't
        # have asyncpg installed.
        from app.database import AsyncSessionLocal

        factory = AsyncSessionLocal
    latency_ms = _compute_latency_ms(start_time)

    try:
        async with factory() as db:
            await _post_process_in_session(
                db=db,
                request_id=request_id,
                virtual_key_id=virtual_key_id,
                user_id=user_id,
                model_id=model_id,
                provider_id=provider_id,
                litellm_response=litellm_response,
                markup_rate=markup_rate,
                latency_ms=latency_ms,
                is_fallback=is_fallback,
            )
    except Exception as exc:
        # Background tasks must never bubble exceptions back to FastAPI; the
        # customer's response is already on the wire. Log everything we know
        # so the failure is debuggable.
        logger.error(
            "post_process_failed",
            request_id=request_id,
            user_id=str(user_id),
            virtual_key_id=str(virtual_key_id),
            model_id=str(model_id),
            provider_id=str(provider_id),
            error=str(exc),
            exc_info=True,
        )


async def _post_process_in_session(
    *,
    db: AsyncSession,
    request_id: str,
    virtual_key_id: uuid.UUID,
    user_id: uuid.UUID,
    model_id: uuid.UUID,
    provider_id: uuid.UUID,
    litellm_response: dict,
    markup_rate: float,
    latency_ms: int,
    is_fallback: bool,
) -> None:
    """
    Inner work performed inside an open AsyncSession. Splitting this out keeps
    the wrapper above focused on session lifecycle + error handling.

    Commits on success; rolls back and re-raises on any exception so the
    outer wrapper logs a single ``post_process_failed`` event.
    """
    try:
        # 1. Pricing + display names come from the configured model and
        #    provider rows. We deliberately re-fetch these here (rather than
        #    threading them through every layer of the gateway) so the
        #    background task only needs UUIDs from the request handler.
        model = await db.get(Model, model_id)
        provider = await db.get(Provider, provider_id)
        if model is None or provider is None:
            logger.error(
                "post_process_missing_model_or_provider",
                request_id=request_id,
                model_id=str(model_id),
                provider_id=str(provider_id),
                model_found=model is not None,
                provider_found=provider is not None,
            )
            return

        # 2. Token counts and cache-hit detection drive every downstream step.
        prompt_tokens, completion_tokens, total_tokens = _extract_usage(litellm_response)
        cache_hit = _detect_cache_hit(litellm_response)

        # 3. Cache hits are billed at zero (Req 7 AC3); otherwise compute
        #    base cost from the model's per-1M token prices and apply markup.
        if cache_hit:
            base_cost = Decimal("0")
            billed_amount = Decimal("0")
        else:
            base_cost = _compute_base_cost(
                prompt_tokens,
                completion_tokens,
                model.input_price_per_1m,
                model.output_price_per_1m,
            )
            billed_amount = base_cost * (Decimal("1") + Decimal(str(markup_rate)))

        # 4. Insert usage_records row first so settle_credit / provider log
        #    rows can FK to it.
        model_name = _resolve_model_name(litellm_response, model)
        usage_record = UsageRecord(
            virtual_key_id=virtual_key_id,
            user_id=user_id,
            model_id=model.id,
            provider_id=provider.id,
            model_name=model_name,
            provider_name=provider.name,
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
            total_tokens=total_tokens,
            base_cost_usd=base_cost,
            markup_rate=Decimal(str(markup_rate)),
            billed_amount_usd=billed_amount,
            latency_ms=latency_ms,
            cache_hit=cache_hit,
            is_fallback=is_fallback,
            status="success",
        )
        db.add(usage_record)
        await db.flush()

        # 5. Credit settlement and provider balance accounting.
        if cache_hit:
            # Cache hit: don't charge the customer. Release the pre-flight
            # hold so the balance reflects no deduction. Skip provider
            # balance deduction entirely (Req 7 AC3).
            await release_hold(
                user_id=user_id,
                request_id=request_id,
                db=db,
            )
        else:
            await settle_credit(
                user_id=user_id,
                request_id=request_id,
                actual_cost=billed_amount,
                usage_record_id=usage_record.id,
                db=db,
            )
            await deduct_provider_balance(
                provider_id=provider.id,
                amount=base_cost,
                request_id=request_id,
                db=db,
                usage_record_id=usage_record.id,
            )
            # 6. Threshold checks may flip the provider into warning /
            #    hard_stop and emit alert emails (handler registered in
            #    Task 28). Only meaningful after a real deduction.
            await check_thresholds(provider, db)

        # 7. Bump virtual_keys counters. We do this even on cache hits — the
        #    request still happened from the customer's perspective.
        stmt = (
            update(VirtualKey)
            .where(VirtualKey.id == virtual_key_id)
            .values(
                total_requests=VirtualKey.total_requests + 1,
                total_tokens=VirtualKey.total_tokens + total_tokens,
                last_used_at=datetime.now(timezone.utc),
            )
        )
        await db.execute(stmt)

        await db.commit()

        logger.info(
            "post_process_completed",
            request_id=request_id,
            user_id=str(user_id),
            virtual_key_id=str(virtual_key_id),
            model_name=model_name,
            provider_name=provider.name,
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
            total_tokens=total_tokens,
            base_cost_usd=str(base_cost),
            billed_amount_usd=str(billed_amount),
            latency_ms=latency_ms,
            cache_hit=cache_hit,
            is_fallback=is_fallback,
        )
    except Exception:
        await db.rollback()
        raise

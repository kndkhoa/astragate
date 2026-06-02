"""
Markup resolution service.

Resolves the effective markup rate for a given model using a 3-level priority:
  1. Model-level markup (model.markup_rate if not null)
  2. Provider-level markup (markup_config where scope='provider' and provider_id matches)
  3. Global default (markup_config where scope='global')

Resolved rates are cached in Redis with TTL 60s for performance.
Cache is invalidated on any markup update via invalidate_markup_cache().

Requirement 4: AC1, AC2, AC3, AC4, AC5
"""
import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging_config import get_logger
from app.models.model import MarkupConfig, Model
from app.redis_client import get_redis

logger = get_logger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

REDIS_MARKUP_PREFIX = "markup:"
REDIS_MARKUP_TTL = 60  # seconds
DEFAULT_MARKUP_RATE = 0.20  # 20% default if nothing configured


# ── Public API ────────────────────────────────────────────────────────────────


async def resolve_markup_rate(
    model_id: uuid.UUID,
    provider_id: uuid.UUID,
    db: AsyncSession,
) -> float:
    """
    Resolve the effective markup rate for a model.

    Priority (highest to lowest):
      1. model.markup_rate (if not null)
      2. markup_config row with scope='provider' and matching provider_id
      3. markup_config row with scope='global'
      4. Hardcoded default: 0.20 (20%)

    The resolved rate is cached in Redis under key `markup:{model_id}` with TTL 60s.

    Args:
        model_id: UUID of the model being used.
        provider_id: UUID of the provider for the model.
        db: Async database session.

    Returns:
        The markup rate as a float (e.g. 0.20 for 20%).
    """
    # 1. Check Redis cache first
    cached = await _get_cached_rate(model_id)
    if cached is not None:
        logger.debug(
            "markup_cache_hit",
            model_id=str(model_id),
            markup_rate=cached,
        )
        return cached

    # 2. Resolve from database
    rate = await _resolve_from_db(model_id, provider_id, db)

    # 3. Cache the resolved rate
    await _cache_rate(model_id, rate)

    logger.info(
        "markup_resolved",
        model_id=str(model_id),
        provider_id=str(provider_id),
        markup_rate=rate,
    )

    return rate


async def invalidate_markup_cache(model_id: uuid.UUID | None = None) -> None:
    """
    Invalidate cached markup rate(s).

    Args:
        model_id: If provided, invalidate only the cache for this model.
                  If None, invalidate all markup caches (used when global
                  or provider-level markup changes).
    """
    try:
        redis = get_redis()
        if model_id is not None:
            cache_key = f"{REDIS_MARKUP_PREFIX}{model_id}"
            await redis.delete(cache_key)
            logger.info(
                "markup_cache_invalidated",
                model_id=str(model_id),
                scope="single",
            )
        else:
            # Invalidate all markup keys using scan
            pattern = f"{REDIS_MARKUP_PREFIX}*"
            cursor = 0
            deleted_count = 0
            while True:
                cursor, keys = await redis.scan(cursor, match=pattern, count=100)
                if keys:
                    await redis.delete(*keys)
                    deleted_count += len(keys)
                if cursor == 0:
                    break
            logger.info(
                "markup_cache_invalidated",
                scope="all",
                deleted_count=deleted_count,
            )
    except Exception as exc:
        # Non-fatal: cache will expire naturally after TTL
        logger.warning(
            "markup_cache_invalidation_failed",
            model_id=str(model_id) if model_id else "all",
            error=str(exc),
        )


# ── Internal Helpers ──────────────────────────────────────────────────────────


async def _get_cached_rate(model_id: uuid.UUID) -> float | None:
    """Attempt to read the markup rate from Redis cache."""
    try:
        redis = get_redis()
        cache_key = f"{REDIS_MARKUP_PREFIX}{model_id}"
        value = await redis.get(cache_key)
        if value is not None:
            return float(value)
    except Exception as exc:
        logger.warning(
            "markup_cache_read_failed",
            model_id=str(model_id),
            error=str(exc),
        )
    return None


async def _cache_rate(model_id: uuid.UUID, rate: float) -> None:
    """Store the resolved markup rate in Redis with TTL."""
    try:
        redis = get_redis()
        cache_key = f"{REDIS_MARKUP_PREFIX}{model_id}"
        await redis.set(cache_key, str(rate), ex=REDIS_MARKUP_TTL)
    except Exception as exc:
        # Non-fatal: next request will just query DB again
        logger.warning(
            "markup_cache_write_failed",
            model_id=str(model_id),
            error=str(exc),
        )


async def _resolve_from_db(
    model_id: uuid.UUID,
    provider_id: uuid.UUID,
    db: AsyncSession,
) -> float:
    """
    Resolve markup rate from database using the 3-level priority.

    Returns the resolved rate as a float.
    """
    # Priority 1: Model-level markup (model.markup_rate if not null)
    model_result = await db.execute(
        select(Model.markup_rate).where(Model.id == model_id)
    )
    model_markup = model_result.scalar_one_or_none()

    if model_markup is not None:
        logger.debug(
            "markup_source",
            model_id=str(model_id),
            source="model",
            rate=float(model_markup),
        )
        return float(model_markup)

    # Priority 2: Provider-level markup from markup_config
    provider_config_result = await db.execute(
        select(MarkupConfig.markup_rate).where(
            MarkupConfig.scope == "provider",
            MarkupConfig.provider_id == provider_id,
        )
    )
    provider_markup = provider_config_result.scalar_one_or_none()

    if provider_markup is not None:
        logger.debug(
            "markup_source",
            model_id=str(model_id),
            provider_id=str(provider_id),
            source="provider",
            rate=float(provider_markup),
        )
        return float(provider_markup)

    # Priority 3: Global default from markup_config
    global_config_result = await db.execute(
        select(MarkupConfig.markup_rate).where(
            MarkupConfig.scope == "global",
        )
    )
    global_markup = global_config_result.scalar_one_or_none()

    if global_markup is not None:
        logger.debug(
            "markup_source",
            model_id=str(model_id),
            source="global",
            rate=float(global_markup),
        )
        return float(global_markup)

    # Fallback: hardcoded default (should not normally reach here if DB is seeded)
    logger.warning(
        "markup_fallback_to_default",
        model_id=str(model_id),
        provider_id=str(provider_id),
        default_rate=DEFAULT_MARKUP_RATE,
    )
    return DEFAULT_MARKUP_RATE

"""
Virtual Key authentication dependency for gateway routes (/v1/*).

Provides a FastAPI Depends()-compatible callable that:
- Extracts Bearer token from Authorization header
- Computes SHA-256 hash and looks up in Redis cache (fallback to PostgreSQL)
- Returns HTTP 401 if key not found or is_active=false
- Updates last_used_at asynchronously (background task)
- Logs suspicious usage: >10 unique IPs per key per hour

Usage in gateway router:
    from app.middleware.virtual_key_auth import require_virtual_key

    @router.post("/chat/completions")
    async def chat(vk: VirtualKey = Depends(require_virtual_key)):
        ...
"""
import json
import time

from fastapi import BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.logging_config import get_logger
from app.models.virtual_key import VirtualKey
from app.redis_client import get_redis
from app.services.virtual_key import REDIS_VK_PREFIX, REDIS_VK_TTL, hash_key

logger = get_logger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

SUSPICIOUS_IP_THRESHOLD = 10  # Max unique IPs per key per hour before alert
IP_TRACKING_PREFIX = "vk_ips:"
IP_TRACKING_TTL = 3600  # 1 hour


# ── Background Tasks ──────────────────────────────────────────────────────────


async def _update_last_used_at(key_id: str) -> None:
    """Update last_used_at for a virtual key (non-blocking background task)."""
    from datetime import datetime, timezone

    from app.database import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(VirtualKey).where(VirtualKey.id == key_id)
            )
            vk = result.scalar_one_or_none()
            if vk:
                vk.last_used_at = datetime.now(timezone.utc)
                await session.commit()
    except Exception as exc:
        logger.warning(
            "update_last_used_at_failed",
            key_id=key_id,
            error=str(exc),
        )


async def _track_ip_usage(key_id: str, client_ip: str) -> None:
    """
    Track unique IPs per key per hour using Redis sets.

    Key pattern: vk_ips:{key_id}:{hour_window}
    If >10 unique IPs detected, log a security warning.
    """
    try:
        redis = get_redis()
        hour_window = int(time.time()) // 3600
        tracking_key = f"{IP_TRACKING_PREFIX}{key_id}:{hour_window}"

        # Add IP to the set
        await redis.sadd(tracking_key, client_ip)
        # Set TTL if this is a new key (first IP in this window)
        await redis.expire(tracking_key, IP_TRACKING_TTL)

        # Check unique IP count
        unique_ips = await redis.scard(tracking_key)
        if unique_ips > SUSPICIOUS_IP_THRESHOLD:
            logger.warning(
                "suspicious_key_usage",
                key_id=key_id,
                unique_ips=unique_ips,
                client_ip=client_ip,
                hour_window=hour_window,
                event_type="security",
            )
    except Exception as exc:
        # Non-fatal: don't block the request for IP tracking failures
        logger.warning(
            "ip_tracking_failed",
            key_id=key_id,
            error=str(exc),
        )


# ── Cache Helpers ─────────────────────────────────────────────────────────────


async def _get_key_from_cache(key_hash: str):
    """
    Try to get virtual key data from Redis cache.

    Returns dict with key data if found, None otherwise.
    """
    try:
        redis = get_redis()
        cache_key = f"{REDIS_VK_PREFIX}{key_hash}"
        cached = await redis.get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception as exc:
        logger.warning("redis_cache_read_failed", error=str(exc))
    return None


async def _set_key_in_cache(key_hash: str, key_data: dict) -> None:
    """Store virtual key data in Redis cache with TTL."""
    try:
        redis = get_redis()
        cache_key = f"{REDIS_VK_PREFIX}{key_hash}"
        await redis.set(cache_key, json.dumps(key_data), ex=REDIS_VK_TTL)
    except Exception as exc:
        logger.warning("redis_cache_write_failed", error=str(exc))


# ── Main Dependency ───────────────────────────────────────────────────────────


async def require_virtual_key(
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> VirtualKey:
    """
    FastAPI dependency that authenticates requests using Virtual Keys.

    Extracts Bearer token from Authorization header, computes SHA-256 hash,
    looks up in Redis cache (fallback to PostgreSQL), and returns the
    VirtualKey model if valid.

    Raises HTTP 401 if:
    - No Authorization header present
    - Token format is invalid
    - Key not found in database
    - Key is inactive (revoked)
    """
    # Extract Bearer token from Authorization header
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header. Use: Authorization: Bearer <virtual_key>",
        )

    parts = auth_header.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header format. Use: Authorization: Bearer <virtual_key>",
        )

    bearer_token = parts[1].strip()
    if not bearer_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Empty bearer token",
        )

    # Compute SHA-256 hash of the token
    key_hash = hash_key(bearer_token)

    # Try Redis cache first
    cached_data = await _get_key_from_cache(key_hash)

    if cached_data is not None:
        # Validate cached key is active
        if not cached_data.get("is_active", False):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Virtual key has been revoked",
            )

        # Reconstruct a minimal VirtualKey-like object from cache for downstream use
        # We still need the full ORM object for downstream pipeline steps
        result = await db.execute(
            select(VirtualKey).where(
                VirtualKey.key_hash == key_hash,
            )
        )
        virtual_key = result.scalar_one_or_none()

        if virtual_key is None:
            # Cache is stale — key was deleted from DB
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid virtual key",
            )
    else:
        # Fallback to PostgreSQL lookup
        result = await db.execute(
            select(VirtualKey).where(
                VirtualKey.key_hash == key_hash,
            )
        )
        virtual_key = result.scalar_one_or_none()

        if virtual_key is None:
            logger.info(
                "virtual_key_auth_failed",
                reason="key_not_found",
                key_hash_prefix=key_hash[:8],
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid virtual key",
            )

        # Cache the key data for future requests
        key_cache_data = {
            "id": str(virtual_key.id),
            "user_id": str(virtual_key.user_id),
            "is_active": virtual_key.is_active,
            "rate_limit_rpm": virtual_key.rate_limit_rpm,
            "key_prefix": virtual_key.key_prefix,
        }
        await _set_key_in_cache(key_hash, key_cache_data)

    # Check if key is active
    if not virtual_key.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Virtual key has been revoked",
        )

    # Attach virtual key to request state for downstream access
    request.state.virtual_key = virtual_key

    # Get client IP for tracking
    client_ip = request.client.host if request.client else "unknown"

    # Schedule background tasks (non-blocking)
    background_tasks.add_task(_update_last_used_at, str(virtual_key.id))
    background_tasks.add_task(_track_ip_usage, str(virtual_key.id), client_ip)

    return virtual_key

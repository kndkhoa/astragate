"""
Rate limiting middleware for gateway routes (/v1/*).

Implements per-Virtual-Key fixed-window rate limiting using Redis. The fixed
window is one minute wide and keys are kept for 120 seconds (covers current +
previous window) so the limiter degrades gracefully across boundaries.

Behavior:
- Skips the check entirely when `virtual_key.rate_limit_rpm` is null (unlimited).
- On every request, increments the counter for the current minute window.
- If the count exceeds `rate_limit_rpm`, raises HTTP 429 with a `Retry-After`
  header indicating the seconds until the next window starts.

Usage in gateway router:
    from app.middleware.rate_limit import enforce_rate_limit

    router = APIRouter(dependencies=[Depends(enforce_rate_limit)])

`enforce_rate_limit` depends on `require_virtual_key`, so wiring it as the
single router dependency authenticates the key AND applies the rate limit.

Key pattern (Redis): rate_limit:{virtual_key_id}:{minute_window}
TTL: 120 seconds
"""
import time

from fastapi import Depends, HTTPException, status

from app.logging_config import get_logger
from app.middleware.virtual_key_auth import require_virtual_key
from app.models.virtual_key import VirtualKey
from app.redis_client import get_redis

logger = get_logger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

RATE_LIMIT_PREFIX = "rate_limit:"
RATE_LIMIT_TTL = 120  # seconds — covers current + previous window
WINDOW_SECONDS = 60


def _build_key(virtual_key_id: str, minute_window: int) -> str:
    """Build the Redis key for a given virtual key and minute window."""
    return f"{RATE_LIMIT_PREFIX}{virtual_key_id}:{minute_window}"


async def _incr_window(virtual_key_id: str, minute_window: int) -> int:
    """
    Increment the rate-limit counter for the given window and return new count.

    Sets a 120s TTL on first write so stale windows expire automatically.
    """
    redis = get_redis()
    redis_key = _build_key(virtual_key_id, minute_window)

    count = await redis.incr(redis_key)
    if count == 1:
        # First request in this window — set TTL.
        await redis.expire(redis_key, RATE_LIMIT_TTL)
    return int(count)


async def enforce_rate_limit(
    virtual_key: VirtualKey = Depends(require_virtual_key),
) -> VirtualKey:
    """
    FastAPI dependency that enforces per-Virtual-Key rate limiting.

    - Skips the check if `rate_limit_rpm` is null (unlimited).
    - Otherwise increments the per-minute counter and raises HTTP 429
      with `Retry-After` if the limit is exceeded.

    Returns the authenticated VirtualKey for downstream handlers.
    """
    # Unlimited keys — pass through.
    if virtual_key.rate_limit_rpm is None:
        return virtual_key

    now = time.time()
    minute_window = int(now) // WINDOW_SECONDS
    key_id = str(virtual_key.id)

    try:
        count = await _incr_window(key_id, minute_window)
    except Exception as exc:
        # Fail open on Redis errors — log and let the request proceed rather
        # than block legitimate traffic when the cache is unavailable.
        logger.warning(
            "rate_limit_redis_error",
            key_id=key_id,
            error=str(exc),
        )
        return virtual_key

    limit = virtual_key.rate_limit_rpm
    if count > limit:
        # Seconds until the start of the next minute window.
        retry_after = max(1, WINDOW_SECONDS - int(now) % WINDOW_SECONDS)
        logger.info(
            "rate_limit_exceeded",
            key_id=key_id,
            key_prefix=virtual_key.key_prefix,
            count=count,
            limit=limit,
            retry_after=retry_after,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Rate limit exceeded: {limit} requests per minute. "
                f"Retry after {retry_after} seconds."
            ),
            headers={"Retry-After": str(retry_after)},
        )

    return virtual_key

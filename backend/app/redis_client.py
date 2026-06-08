from typing import Any, Dict, Optional, Tuple, List
import fnmatch
import structlog

import redis.asyncio as aioredis

from app.config import settings

logger = structlog.get_logger(__name__)

class MockRedis:
    def __init__(self):
        self._store: Dict[str, str] = {}
        logger.info("redis_mock_initialized", message="Using in-memory MockRedis fallback client.")

    async def ping(self) -> bool:
        return True

    async def get(self, key: str) -> Optional[str]:
        return self._store.get(key)

    async def set(
        self,
        key: str,
        value: Any,
        ex: Optional[int] = None,
        px: Optional[int] = None,
        nx: bool = False,
        xx: bool = False,
    ) -> bool:
        self._store[key] = str(value)
        return True

    async def setex(self, key: str, time: int, value: Any) -> bool:
        self._store[key] = str(value)
        return True

    async def delete(self, *keys: str) -> int:
        count = 0
        for k in keys:
            if k in self._store:
                del self._store[k]
                count += 1
        return count

    async def incr(self, key: str, amount: int = 1) -> int:
        val = int(self._store.get(key, 0))
        new_val = val + amount
        self._store[key] = str(new_val)
        return new_val

    async def expire(self, key: str, time: int) -> bool:
        return True

    async def scan(
        self, cursor: int, match: Optional[str] = None, count: int = 10
    ) -> Tuple[int, List[str]]:
        keys = list(self._store.keys())
        if match:
            matched_keys = fnmatch.filter(keys, match)
        else:
            matched_keys = keys
        return 0, matched_keys

    async def aclose(self) -> None:
        pass


# Module-level client instance (initialized on startup)
_redis_client: Optional[aioredis.Redis] = None


async def init_redis() -> aioredis.Redis:
    """Initialize and return the Redis client."""
    global _redis_client
    try:
        _redis_client = aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            max_connections=20,
        )
        # Verify connection
        await _redis_client.ping()
    except Exception as exc:
        logger.warning(
            "redis_connection_failed_falling_back_to_mock",
            url=settings.REDIS_URL,
            error=str(exc),
        )
        _redis_client = MockRedis()  # type: ignore
    return _redis_client


async def close_redis() -> None:
    """Close the Redis connection."""
    global _redis_client
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None


def get_redis() -> aioredis.Redis:
    """Get the Redis client instance. Must be called after init_redis()."""
    if _redis_client is None:
        raise RuntimeError("Redis client not initialized. Call init_redis() first.")
    return _redis_client

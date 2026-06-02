"""
Unit tests for the rate limiting middleware.

Covers:
- Skip path when rate_limit_rpm is null (unlimited)
- Counter increments + TTL set on first request in a window
- HTTP 429 with Retry-After header when limit exceeded
- Fail-open behavior when Redis errors
- Redis key pattern correctness
"""
import time
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.middleware.rate_limit import (
    RATE_LIMIT_PREFIX,
    RATE_LIMIT_TTL,
    WINDOW_SECONDS,
    _build_key,
    _incr_window,
    enforce_rate_limit,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_vk(rate_limit_rpm=None, key_id=None, key_prefix="ag-sk-ab"):
    """Construct a mock VirtualKey for the dependency call."""
    vk = MagicMock()
    vk.id = key_id or uuid.uuid4()
    vk.rate_limit_rpm = rate_limit_rpm
    vk.key_prefix = key_prefix
    return vk


# ── Tests: Key Pattern ────────────────────────────────────────────────────────


class TestKeyPattern:
    def test_build_key_format(self):
        """Redis key follows rate_limit:{vk_id}:{minute_window}."""
        key = _build_key("abc-123", 29384782)
        assert key == f"{RATE_LIMIT_PREFIX}abc-123:29384782"


# ── Tests: _incr_window ───────────────────────────────────────────────────────


class TestIncrWindow:
    @pytest.mark.asyncio
    @patch("app.middleware.rate_limit.get_redis")
    async def test_first_request_sets_ttl(self, mock_get_redis):
        """First request in window → INCR returns 1 → EXPIRE called."""
        redis = AsyncMock()
        redis.incr = AsyncMock(return_value=1)
        redis.expire = AsyncMock()
        mock_get_redis.return_value = redis

        count = await _incr_window("vk-1", 100)

        assert count == 1
        redis.incr.assert_awaited_once_with(f"{RATE_LIMIT_PREFIX}vk-1:100")
        redis.expire.assert_awaited_once_with(
            f"{RATE_LIMIT_PREFIX}vk-1:100", RATE_LIMIT_TTL
        )

    @pytest.mark.asyncio
    @patch("app.middleware.rate_limit.get_redis")
    async def test_subsequent_requests_skip_expire(self, mock_get_redis):
        """Subsequent requests (count > 1) → EXPIRE not called again."""
        redis = AsyncMock()
        redis.incr = AsyncMock(return_value=5)
        redis.expire = AsyncMock()
        mock_get_redis.return_value = redis

        count = await _incr_window("vk-1", 100)

        assert count == 5
        redis.expire.assert_not_called()


# ── Tests: enforce_rate_limit ─────────────────────────────────────────────────


class TestEnforceRateLimit:
    @pytest.mark.asyncio
    async def test_unlimited_key_skips_check(self):
        """rate_limit_rpm is None → no Redis call, returns the key."""
        vk = _make_vk(rate_limit_rpm=None)

        with patch("app.middleware.rate_limit.get_redis") as mock_get_redis:
            mock_get_redis.return_value = AsyncMock()
            result = await enforce_rate_limit(virtual_key=vk)

        assert result is vk
        mock_get_redis.assert_not_called()

    @pytest.mark.asyncio
    @patch("app.middleware.rate_limit._incr_window")
    async def test_under_limit_returns_key(self, mock_incr):
        """count <= limit → returns the VirtualKey."""
        mock_incr.return_value = 5
        vk = _make_vk(rate_limit_rpm=10)

        result = await enforce_rate_limit(virtual_key=vk)

        assert result is vk
        mock_incr.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("app.middleware.rate_limit._incr_window")
    async def test_at_limit_returns_key(self, mock_incr):
        """count == limit is allowed (boundary)."""
        mock_incr.return_value = 10
        vk = _make_vk(rate_limit_rpm=10)

        result = await enforce_rate_limit(virtual_key=vk)
        assert result is vk

    @pytest.mark.asyncio
    @patch("app.middleware.rate_limit._incr_window")
    async def test_over_limit_raises_429_with_retry_after(self, mock_incr):
        """count > limit → HTTPException 429 with Retry-After header."""
        mock_incr.return_value = 11
        vk = _make_vk(rate_limit_rpm=10)

        with pytest.raises(HTTPException) as exc_info:
            await enforce_rate_limit(virtual_key=vk)

        exc = exc_info.value
        assert exc.status_code == 429
        assert "Retry-After" in exc.headers
        retry_after = int(exc.headers["Retry-After"])
        assert 1 <= retry_after <= WINDOW_SECONDS
        assert "Rate limit exceeded" in exc.detail

    @pytest.mark.asyncio
    @patch("app.middleware.rate_limit._incr_window")
    async def test_uses_current_minute_window(self, mock_incr):
        """The minute_window passed to _incr_window matches int(time)//60."""
        mock_incr.return_value = 1
        vk = _make_vk(rate_limit_rpm=10)

        before = int(time.time()) // WINDOW_SECONDS
        await enforce_rate_limit(virtual_key=vk)
        after = int(time.time()) // WINDOW_SECONDS

        # The window passed should be one of {before, after} (covers boundary)
        called_window = mock_incr.call_args.args[1]
        assert called_window in (before, after)

    @pytest.mark.asyncio
    @patch("app.middleware.rate_limit._incr_window")
    async def test_redis_failure_fails_open(self, mock_incr):
        """Redis error → log + allow request (fail-open)."""
        mock_incr.side_effect = Exception("Redis down")
        vk = _make_vk(rate_limit_rpm=10)

        # Should not raise — fails open.
        result = await enforce_rate_limit(virtual_key=vk)
        assert result is vk

    @pytest.mark.asyncio
    @patch("app.middleware.rate_limit._incr_window")
    async def test_retry_after_capped_to_window_seconds(self, mock_incr):
        """Retry-After is bounded between 1 and WINDOW_SECONDS."""
        mock_incr.return_value = 100
        vk = _make_vk(rate_limit_rpm=1)

        with pytest.raises(HTTPException) as exc_info:
            await enforce_rate_limit(virtual_key=vk)

        retry_after = int(exc_info.value.headers["Retry-After"])
        assert 1 <= retry_after <= WINDOW_SECONDS

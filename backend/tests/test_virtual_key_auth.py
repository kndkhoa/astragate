"""
Unit tests for the Virtual Key auth middleware.

Tests token extraction, hash lookup, cache behavior, error responses,
and background task scheduling.
"""
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.middleware.virtual_key_auth import (
    IP_TRACKING_PREFIX,
    SUSPICIOUS_IP_THRESHOLD,
    _get_key_from_cache,
    _set_key_in_cache,
    _track_ip_usage,
    _update_last_used_at,
    require_virtual_key,
)
from app.services.virtual_key import REDIS_VK_PREFIX, REDIS_VK_TTL, hash_key


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_virtual_key(
    key_id=None, user_id=None, is_active=True, key_hash="abc123", key_prefix="ag-sk-ab"
):
    """Create a mock VirtualKey object."""
    vk = MagicMock()
    vk.id = key_id or uuid.uuid4()
    vk.user_id = user_id or uuid.uuid4()
    vk.is_active = is_active
    vk.key_hash = key_hash
    vk.key_prefix = key_prefix
    vk.rate_limit_rpm = None
    vk.last_used_at = None
    return vk


def _make_request(auth_header=None, client_ip="192.168.1.1"):
    """Create a mock FastAPI Request."""
    request = MagicMock()
    headers_dict = {}
    if auth_header is not None:
        headers_dict["Authorization"] = auth_header
    request.headers = MagicMock()
    request.headers.get = lambda key, default=None: headers_dict.get(key, default)
    request.client = MagicMock()
    request.client.host = client_ip
    request.state = MagicMock()
    return request


def _make_background_tasks():
    """Create a mock BackgroundTasks."""
    bt = MagicMock()
    bt.add_task = MagicMock()
    return bt


# ── Tests: Token Extraction ───────────────────────────────────────────────────


class TestTokenExtraction:
    """Tests for Authorization header parsing."""

    @pytest.mark.asyncio
    async def test_missing_auth_header_returns_401(self):
        """No Authorization header → 401."""
        request = _make_request(auth_header=None)
        bg = _make_background_tasks()
        db = AsyncMock()

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await require_virtual_key(request, bg, db)
        assert exc_info.value.status_code == 401
        assert "Missing Authorization header" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_invalid_format_no_bearer_prefix(self):
        """Authorization header without 'Bearer' prefix → 401."""
        request = _make_request(auth_header="Token abc123")
        bg = _make_background_tasks()
        db = AsyncMock()

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await require_virtual_key(request, bg, db)
        assert exc_info.value.status_code == 401
        assert "Invalid Authorization header format" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_empty_bearer_token(self):
        """Authorization: Bearer (empty) → 401."""
        request = _make_request(auth_header="Bearer ")
        bg = _make_background_tasks()
        db = AsyncMock()

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await require_virtual_key(request, bg, db)
        assert exc_info.value.status_code == 401
        assert "Empty bearer token" in exc_info.value.detail


# ── Tests: Key Lookup ─────────────────────────────────────────────────────────


class TestKeyLookup:
    """Tests for Redis cache + PostgreSQL fallback lookup."""

    @pytest.mark.asyncio
    @patch("app.middleware.virtual_key_auth._get_key_from_cache")
    @patch("app.middleware.virtual_key_auth._set_key_in_cache")
    async def test_key_not_found_returns_401(self, mock_set_cache, mock_get_cache):
        """Key hash not in cache or DB → 401."""
        mock_get_cache.return_value = None

        request = _make_request(auth_header="Bearer ag-sk-test1234567890abcdef12345678")
        bg = _make_background_tasks()

        # Mock DB returning no result
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=mock_result)

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await require_virtual_key(request, bg, db)
        assert exc_info.value.status_code == 401
        assert "Invalid virtual key" in exc_info.value.detail

    @pytest.mark.asyncio
    @patch("app.middleware.virtual_key_auth._get_key_from_cache")
    @patch("app.middleware.virtual_key_auth._set_key_in_cache")
    async def test_revoked_key_returns_401(self, mock_set_cache, mock_get_cache):
        """Key found in DB but is_active=False → 401."""
        mock_get_cache.return_value = None

        request = _make_request(auth_header="Bearer ag-sk-test1234567890abcdef12345678")
        bg = _make_background_tasks()

        # Mock DB returning inactive key
        vk = _make_virtual_key(is_active=False)
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = vk
        db.execute = AsyncMock(return_value=mock_result)

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await require_virtual_key(request, bg, db)
        assert exc_info.value.status_code == 401
        assert "revoked" in exc_info.value.detail

    @pytest.mark.asyncio
    @patch("app.middleware.virtual_key_auth._get_key_from_cache")
    @patch("app.middleware.virtual_key_auth._set_key_in_cache")
    async def test_valid_key_from_db_returns_key(self, mock_set_cache, mock_get_cache):
        """Valid active key found in DB → returns VirtualKey, caches it."""
        mock_get_cache.return_value = None

        token = "ag-sk-test1234567890abcdef12345678"
        request = _make_request(auth_header=f"Bearer {token}")
        bg = _make_background_tasks()

        vk = _make_virtual_key(is_active=True, key_hash=hash_key(token))
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = vk
        db.execute = AsyncMock(return_value=mock_result)

        result = await require_virtual_key(request, bg, db)

        assert result == vk
        # Should cache the key
        mock_set_cache.assert_called_once()
        # Should schedule background tasks
        assert bg.add_task.call_count == 2

    @pytest.mark.asyncio
    @patch("app.middleware.virtual_key_auth._get_key_from_cache")
    async def test_cached_active_key_still_queries_db(self, mock_get_cache):
        """Cache hit with active key → still fetches full ORM object from DB."""
        key_id = uuid.uuid4()
        user_id = uuid.uuid4()
        mock_get_cache.return_value = {
            "id": str(key_id),
            "user_id": str(user_id),
            "is_active": True,
            "rate_limit_rpm": None,
            "key_prefix": "ag-sk-te",
        }

        token = "ag-sk-test1234567890abcdef12345678"
        request = _make_request(auth_header=f"Bearer {token}")
        bg = _make_background_tasks()

        vk = _make_virtual_key(key_id=key_id, user_id=user_id, is_active=True)
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = vk
        db.execute = AsyncMock(return_value=mock_result)

        result = await require_virtual_key(request, bg, db)
        assert result == vk

    @pytest.mark.asyncio
    @patch("app.middleware.virtual_key_auth._get_key_from_cache")
    async def test_cached_revoked_key_returns_401(self, mock_get_cache):
        """Cache hit with is_active=False → 401 immediately."""
        mock_get_cache.return_value = {
            "id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "is_active": False,
            "rate_limit_rpm": None,
            "key_prefix": "ag-sk-te",
        }

        request = _make_request(auth_header="Bearer ag-sk-test1234567890abcdef12345678")
        bg = _make_background_tasks()
        db = AsyncMock()

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await require_virtual_key(request, bg, db)
        assert exc_info.value.status_code == 401
        assert "revoked" in exc_info.value.detail


# ── Tests: Background Tasks ───────────────────────────────────────────────────


class TestBackgroundTasks:
    """Tests for async last_used_at update and IP tracking."""

    @pytest.mark.asyncio
    @patch("app.middleware.virtual_key_auth._get_key_from_cache")
    @patch("app.middleware.virtual_key_auth._set_key_in_cache")
    async def test_background_tasks_scheduled(self, mock_set_cache, mock_get_cache):
        """Valid key → schedules update_last_used_at and track_ip_usage."""
        mock_get_cache.return_value = None

        token = "ag-sk-test1234567890abcdef12345678"
        request = _make_request(auth_header=f"Bearer {token}", client_ip="10.0.0.1")
        bg = _make_background_tasks()

        vk = _make_virtual_key(is_active=True, key_hash=hash_key(token))
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = vk
        db.execute = AsyncMock(return_value=mock_result)

        await require_virtual_key(request, bg, db)

        # Two background tasks: update_last_used_at and track_ip_usage
        assert bg.add_task.call_count == 2
        task_funcs = [call.args[0] for call in bg.add_task.call_args_list]
        assert _update_last_used_at in task_funcs
        assert _track_ip_usage in task_funcs


# ── Tests: IP Tracking ────────────────────────────────────────────────────────


class TestIPTracking:
    """Tests for suspicious IP usage detection."""

    @pytest.mark.asyncio
    @patch("app.middleware.virtual_key_auth.get_redis")
    async def test_ip_added_to_redis_set(self, mock_get_redis):
        """IP is added to the tracking set in Redis."""
        redis_mock = AsyncMock()
        redis_mock.sadd = AsyncMock()
        redis_mock.expire = AsyncMock()
        redis_mock.scard = AsyncMock(return_value=1)
        mock_get_redis.return_value = redis_mock

        await _track_ip_usage("key-123", "192.168.1.1")

        redis_mock.sadd.assert_called_once()
        redis_mock.expire.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.middleware.virtual_key_auth.get_redis")
    @patch("app.middleware.virtual_key_auth.logger")
    async def test_suspicious_usage_logged(self, mock_logger, mock_get_redis):
        """More than 10 unique IPs → security warning logged."""
        redis_mock = AsyncMock()
        redis_mock.sadd = AsyncMock()
        redis_mock.expire = AsyncMock()
        redis_mock.scard = AsyncMock(return_value=SUSPICIOUS_IP_THRESHOLD + 1)
        mock_get_redis.return_value = redis_mock

        await _track_ip_usage("key-123", "192.168.1.99")

        mock_logger.warning.assert_called_once()
        call_args = mock_logger.warning.call_args
        assert call_args.args[0] == "suspicious_key_usage"

    @pytest.mark.asyncio
    @patch("app.middleware.virtual_key_auth.get_redis")
    @patch("app.middleware.virtual_key_auth.logger")
    async def test_ip_tracking_failure_non_fatal(self, mock_logger, mock_get_redis):
        """Redis failure in IP tracking doesn't raise."""
        redis_mock = AsyncMock()
        redis_mock.sadd = AsyncMock(side_effect=Exception("Redis down"))
        mock_get_redis.return_value = redis_mock

        # Should not raise
        await _track_ip_usage("key-123", "192.168.1.1")

        mock_logger.warning.assert_called_once()


# ── Tests: Cache Helpers ──────────────────────────────────────────────────────


class TestCacheHelpers:
    """Tests for Redis cache read/write."""

    @pytest.mark.asyncio
    @patch("app.middleware.virtual_key_auth.get_redis")
    async def test_get_key_from_cache_hit(self, mock_get_redis):
        """Cache hit returns parsed JSON data."""
        key_data = {"id": "abc", "is_active": True}
        redis_mock = AsyncMock()
        redis_mock.get = AsyncMock(return_value=json.dumps(key_data))
        mock_get_redis.return_value = redis_mock

        result = await _get_key_from_cache("somehash")
        assert result == key_data

    @pytest.mark.asyncio
    @patch("app.middleware.virtual_key_auth.get_redis")
    async def test_get_key_from_cache_miss(self, mock_get_redis):
        """Cache miss returns None."""
        redis_mock = AsyncMock()
        redis_mock.get = AsyncMock(return_value=None)
        mock_get_redis.return_value = redis_mock

        result = await _get_key_from_cache("somehash")
        assert result is None

    @pytest.mark.asyncio
    @patch("app.middleware.virtual_key_auth.get_redis")
    async def test_get_key_from_cache_redis_error(self, mock_get_redis):
        """Redis error returns None (non-fatal)."""
        redis_mock = AsyncMock()
        redis_mock.get = AsyncMock(side_effect=Exception("Connection refused"))
        mock_get_redis.return_value = redis_mock

        result = await _get_key_from_cache("somehash")
        assert result is None

    @pytest.mark.asyncio
    @patch("app.middleware.virtual_key_auth.get_redis")
    async def test_set_key_in_cache(self, mock_get_redis):
        """Cache write stores JSON with correct TTL."""
        redis_mock = AsyncMock()
        redis_mock.set = AsyncMock()
        mock_get_redis.return_value = redis_mock

        key_data = {"id": "abc", "is_active": True}
        await _set_key_in_cache("somehash", key_data)

        redis_mock.set.assert_called_once_with(
            f"{REDIS_VK_PREFIX}somehash",
            json.dumps(key_data),
            ex=REDIS_VK_TTL,
        )

    @pytest.mark.asyncio
    @patch("app.middleware.virtual_key_auth.get_redis")
    async def test_set_key_in_cache_redis_error_non_fatal(self, mock_get_redis):
        """Redis write failure doesn't raise."""
        redis_mock = AsyncMock()
        redis_mock.set = AsyncMock(side_effect=Exception("Redis down"))
        mock_get_redis.return_value = redis_mock

        # Should not raise
        await _set_key_in_cache("somehash", {"id": "abc"})

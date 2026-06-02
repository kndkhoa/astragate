"""
Unit tests for the Markup resolution service.

Tests the 3-level priority resolution, Redis caching, and cache invalidation.
"""
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.markup import (
    DEFAULT_MARKUP_RATE,
    REDIS_MARKUP_PREFIX,
    REDIS_MARKUP_TTL,
    _resolve_from_db,
    invalidate_markup_cache,
    resolve_markup_rate,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def model_id():
    return uuid.uuid4()


@pytest.fixture
def provider_id():
    return uuid.uuid4()


@pytest.fixture
def mock_redis():
    """Create a mock Redis client."""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock()
    redis.delete = AsyncMock()
    redis.scan = AsyncMock(return_value=(0, []))
    return redis


@pytest.fixture
def mock_db():
    """Create a mock async database session."""
    db = AsyncMock()
    return db


def _make_scalar_result(value):
    """Helper to create a mock execute result with scalar_one_or_none."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


# ── Tests: resolve_markup_rate ────────────────────────────────────────────────


class TestResolveMarkupRate:
    """Tests for resolve_markup_rate() — full resolution with caching."""

    @pytest.mark.asyncio
    async def test_returns_cached_value_when_available(
        self, model_id, provider_id, mock_redis, mock_db
    ):
        """If Redis has a cached value, return it without querying DB."""
        mock_redis.get.return_value = "0.35"

        with patch("app.services.markup.get_redis", return_value=mock_redis):
            rate = await resolve_markup_rate(model_id, provider_id, mock_db)

        assert rate == 0.35
        # DB should not be queried
        mock_db.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_queries_db_when_cache_miss(
        self, model_id, provider_id, mock_redis, mock_db
    ):
        """When cache misses, resolve from DB and cache the result."""
        mock_redis.get.return_value = None
        # Model has markup_rate = 0.30
        mock_db.execute.return_value = _make_scalar_result(Decimal("0.30"))

        with patch("app.services.markup.get_redis", return_value=mock_redis):
            rate = await resolve_markup_rate(model_id, provider_id, mock_db)

        assert rate == 0.30
        # Should cache the result
        mock_redis.set.assert_called_once_with(
            f"{REDIS_MARKUP_PREFIX}{model_id}",
            "0.3",
            ex=REDIS_MARKUP_TTL,
        )

    @pytest.mark.asyncio
    async def test_handles_redis_read_failure_gracefully(
        self, model_id, provider_id, mock_redis, mock_db
    ):
        """If Redis read fails, fall through to DB resolution."""
        mock_redis.get.side_effect = Exception("Redis connection error")
        # Model has markup_rate = 0.25
        mock_db.execute.return_value = _make_scalar_result(Decimal("0.25"))

        with patch("app.services.markup.get_redis", return_value=mock_redis):
            rate = await resolve_markup_rate(model_id, provider_id, mock_db)

        assert rate == 0.25

    @pytest.mark.asyncio
    async def test_handles_redis_write_failure_gracefully(
        self, model_id, provider_id, mock_redis, mock_db
    ):
        """If Redis write fails, still return the resolved rate."""
        mock_redis.get.return_value = None
        mock_redis.set.side_effect = Exception("Redis write error")
        mock_db.execute.return_value = _make_scalar_result(Decimal("0.40"))

        with patch("app.services.markup.get_redis", return_value=mock_redis):
            rate = await resolve_markup_rate(model_id, provider_id, mock_db)

        assert rate == 0.40


# ── Tests: _resolve_from_db ───────────────────────────────────────────────────


class TestResolveFromDb:
    """Tests for the 3-level priority resolution logic."""

    @pytest.mark.asyncio
    async def test_priority_1_model_level_markup(self, model_id, provider_id, mock_db):
        """Model-level markup takes highest priority."""
        # First query (model.markup_rate) returns a value
        mock_db.execute.return_value = _make_scalar_result(Decimal("0.50"))

        rate = await _resolve_from_db(model_id, provider_id, mock_db)

        assert rate == 0.50
        # Only one DB query needed
        assert mock_db.execute.call_count == 1

    @pytest.mark.asyncio
    async def test_priority_2_provider_level_markup(
        self, model_id, provider_id, mock_db
    ):
        """Provider-level markup is used when model-level is null."""
        # First query (model.markup_rate) returns None
        # Second query (provider markup_config) returns a value
        mock_db.execute.side_effect = [
            _make_scalar_result(None),  # model.markup_rate is null
            _make_scalar_result(Decimal("0.35")),  # provider config
        ]

        rate = await _resolve_from_db(model_id, provider_id, mock_db)

        assert rate == 0.35
        assert mock_db.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_priority_3_global_default_markup(
        self, model_id, provider_id, mock_db
    ):
        """Global default is used when model and provider levels are null."""
        mock_db.execute.side_effect = [
            _make_scalar_result(None),  # model.markup_rate is null
            _make_scalar_result(None),  # no provider config
            _make_scalar_result(Decimal("0.20")),  # global config
        ]

        rate = await _resolve_from_db(model_id, provider_id, mock_db)

        assert rate == 0.20
        assert mock_db.execute.call_count == 3

    @pytest.mark.asyncio
    async def test_fallback_to_hardcoded_default(
        self, model_id, provider_id, mock_db
    ):
        """If no config exists at all, use hardcoded default 0.20."""
        mock_db.execute.side_effect = [
            _make_scalar_result(None),  # model.markup_rate is null
            _make_scalar_result(None),  # no provider config
            _make_scalar_result(None),  # no global config
        ]

        rate = await _resolve_from_db(model_id, provider_id, mock_db)

        assert rate == DEFAULT_MARKUP_RATE
        assert rate == 0.20

    @pytest.mark.asyncio
    async def test_model_markup_zero_is_valid(self, model_id, provider_id, mock_db):
        """Markup rate of 0.0 (pass-through) should be used, not skipped."""
        # Model has markup_rate = 0.0 (pass-through pricing)
        mock_db.execute.return_value = _make_scalar_result(Decimal("0.0"))

        rate = await _resolve_from_db(model_id, provider_id, mock_db)

        # 0.0 is a valid rate (AC4: allow 0% pass-through)
        assert rate == 0.0
        assert mock_db.execute.call_count == 1

    @pytest.mark.asyncio
    async def test_high_markup_rate(self, model_id, provider_id, mock_db):
        """Markup rate up to 5.0 (500%) is valid."""
        mock_db.execute.return_value = _make_scalar_result(Decimal("5.0"))

        rate = await _resolve_from_db(model_id, provider_id, mock_db)

        assert rate == 5.0


# ── Tests: invalidate_markup_cache ────────────────────────────────────────────


class TestInvalidateMarkupCache:
    """Tests for cache invalidation."""

    @pytest.mark.asyncio
    async def test_invalidate_single_model(self, model_id, mock_redis):
        """Invalidating a specific model deletes only that key."""
        with patch("app.services.markup.get_redis", return_value=mock_redis):
            await invalidate_markup_cache(model_id)

        mock_redis.delete.assert_called_once_with(
            f"{REDIS_MARKUP_PREFIX}{model_id}"
        )

    @pytest.mark.asyncio
    async def test_invalidate_all_models(self, mock_redis):
        """Invalidating without model_id scans and deletes all markup keys."""
        # Simulate scan returning some keys then finishing
        mock_redis.scan.return_value = (
            0,
            [f"{REDIS_MARKUP_PREFIX}abc", f"{REDIS_MARKUP_PREFIX}def"],
        )

        with patch("app.services.markup.get_redis", return_value=mock_redis):
            await invalidate_markup_cache(None)

        mock_redis.scan.assert_called()
        mock_redis.delete.assert_called_once_with(
            f"{REDIS_MARKUP_PREFIX}abc",
            f"{REDIS_MARKUP_PREFIX}def",
        )

    @pytest.mark.asyncio
    async def test_invalidate_handles_redis_failure(self, model_id, mock_redis):
        """Cache invalidation failure is non-fatal."""
        mock_redis.delete.side_effect = Exception("Redis down")

        with patch("app.services.markup.get_redis", return_value=mock_redis):
            # Should not raise
            await invalidate_markup_cache(model_id)


# ── Tests: Constants ──────────────────────────────────────────────────────────


class TestConstants:
    """Verify service constants match design requirements."""

    def test_cache_ttl_is_60_seconds(self):
        assert REDIS_MARKUP_TTL == 60

    def test_default_markup_rate_is_20_percent(self):
        assert DEFAULT_MARKUP_RATE == 0.20

    def test_cache_key_prefix(self):
        assert REDIS_MARKUP_PREFIX == "markup:"

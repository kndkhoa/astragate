"""
Unit tests for admin markup configuration endpoints and service.

Tests the admin markup CRUD operations: global, provider, and model-level
markup updates, and the GET /admin/models listing with resolved markup.

Requirement 4: AC1, AC5, AC6
Requirement 11: AC7
"""
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.admin_markup import (
    list_models_with_markup,
    update_global_markup,
    update_model_markup,
    update_provider_markup,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


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
    db.add = MagicMock()
    db.flush = AsyncMock()
    return db


@pytest.fixture
def provider_id():
    return uuid.uuid4()


@pytest.fixture
def model_id():
    return uuid.uuid4()


def _make_scalar_result(value):
    """Helper to create a mock execute result with scalar_one_or_none."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _make_scalars_result(values):
    """Helper to create a mock execute result with scalars().all()."""
    result = MagicMock()
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = values
    result.scalars.return_value = scalars_mock
    return result


# ── Tests: update_global_markup ───────────────────────────────────────────────


class TestUpdateGlobalMarkup:
    """Tests for update_global_markup service function."""

    @pytest.mark.asyncio
    async def test_creates_global_config_when_none_exists(self, mock_db, mock_redis):
        """When no global config exists, create a new one."""
        mock_db.execute.return_value = _make_scalar_result(None)

        with patch("app.services.admin_markup.invalidate_markup_cache", new_callable=AsyncMock):
            result = await update_global_markup(markup_rate=0.30, db=mock_db)

        assert result["scope"] == "global"
        assert result["markup_rate"] == 0.30
        mock_db.add.assert_called_once()
        mock_db.flush.assert_called_once()

    @pytest.mark.asyncio
    async def test_updates_existing_global_config(self, mock_db, mock_redis):
        """When global config exists, update it."""
        existing_config = MagicMock()
        existing_config.markup_rate = Decimal("0.20")
        existing_config.updated_at = None
        mock_db.execute.return_value = _make_scalar_result(existing_config)

        with patch("app.services.admin_markup.invalidate_markup_cache", new_callable=AsyncMock):
            result = await update_global_markup(markup_rate=0.50, db=mock_db)

        assert result["scope"] == "global"
        assert existing_config.markup_rate == Decimal("0.5")
        mock_db.flush.assert_called_once()

    @pytest.mark.asyncio
    async def test_invalidates_all_caches(self, mock_db, mock_redis):
        """Global markup update invalidates all markup caches."""
        mock_db.execute.return_value = _make_scalar_result(None)

        with patch(
            "app.services.admin_markup.invalidate_markup_cache", new_callable=AsyncMock
        ) as mock_invalidate:
            await update_global_markup(markup_rate=0.25, db=mock_db)

        mock_invalidate.assert_called_once_with(model_id=None)


# ── Tests: update_provider_markup ─────────────────────────────────────────────


class TestUpdateProviderMarkup:
    """Tests for update_provider_markup service function."""

    @pytest.mark.asyncio
    async def test_raises_value_error_for_missing_provider(self, mock_db, provider_id):
        """Should raise ValueError if provider not found."""
        # First call: provider lookup returns None
        mock_db.execute.return_value = _make_scalar_result(None)

        with patch("app.services.admin_markup.invalidate_markup_cache", new_callable=AsyncMock):
            with pytest.raises(ValueError, match="Provider not found"):
                await update_provider_markup(
                    provider_id=provider_id, markup_rate=0.30, db=mock_db
                )

    @pytest.mark.asyncio
    async def test_creates_provider_config_when_none_exists(self, mock_db, provider_id):
        """When no provider config exists, create a new one."""
        provider_mock = MagicMock()
        provider_mock.id = provider_id
        provider_mock.name = "groq"

        # First call: provider lookup
        # Second call: markup_config lookup returns None
        mock_db.execute.side_effect = [
            _make_scalar_result(provider_mock),
            _make_scalar_result(None),
        ]

        with patch("app.services.admin_markup.invalidate_markup_cache", new_callable=AsyncMock):
            result = await update_provider_markup(
                provider_id=provider_id, markup_rate=0.35, db=mock_db
            )

        assert result["scope"] == "provider"
        assert result["provider_id"] == str(provider_id)
        assert result["markup_rate"] == 0.35
        mock_db.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_updates_existing_provider_config(self, mock_db, provider_id):
        """When provider config exists, update it."""
        provider_mock = MagicMock()
        provider_mock.id = provider_id
        provider_mock.name = "groq"

        existing_config = MagicMock()
        existing_config.markup_rate = Decimal("0.20")
        existing_config.updated_at = None

        mock_db.execute.side_effect = [
            _make_scalar_result(provider_mock),
            _make_scalar_result(existing_config),
        ]

        with patch("app.services.admin_markup.invalidate_markup_cache", new_callable=AsyncMock):
            result = await update_provider_markup(
                provider_id=provider_id, markup_rate=0.45, db=mock_db
            )

        assert result["markup_rate"] == 0.45
        assert existing_config.markup_rate == Decimal("0.45")

    @pytest.mark.asyncio
    async def test_invalidates_all_caches(self, mock_db, provider_id):
        """Provider markup update invalidates all markup caches."""
        provider_mock = MagicMock()
        provider_mock.id = provider_id
        provider_mock.name = "groq"

        mock_db.execute.side_effect = [
            _make_scalar_result(provider_mock),
            _make_scalar_result(None),
        ]

        with patch(
            "app.services.admin_markup.invalidate_markup_cache", new_callable=AsyncMock
        ) as mock_invalidate:
            await update_provider_markup(
                provider_id=provider_id, markup_rate=0.30, db=mock_db
            )

        mock_invalidate.assert_called_once_with(model_id=None)


# ── Tests: update_model_markup ────────────────────────────────────────────────


class TestUpdateModelMarkup:
    """Tests for update_model_markup service function."""

    @pytest.mark.asyncio
    async def test_raises_value_error_for_missing_model(self, mock_db, model_id):
        """Should raise ValueError if model not found."""
        mock_db.execute.return_value = _make_scalar_result(None)

        with patch("app.services.admin_markup.invalidate_markup_cache", new_callable=AsyncMock):
            with pytest.raises(ValueError, match="Model not found"):
                await update_model_markup(
                    model_id=model_id, markup_rate=0.30, db=mock_db
                )

    @pytest.mark.asyncio
    async def test_sets_model_markup_rate(self, mock_db, model_id):
        """Should set model.markup_rate directly."""
        model_mock = MagicMock()
        model_mock.id = model_id
        model_mock.model_id = "groq/llama-3.1-8b-instant"
        model_mock.display_name = "Llama 3.1 8B"
        model_mock.markup_rate = None
        model_mock.updated_at = None

        mock_db.execute.return_value = _make_scalar_result(model_mock)

        with patch("app.services.admin_markup.invalidate_markup_cache", new_callable=AsyncMock):
            result = await update_model_markup(
                model_id=model_id, markup_rate=0.40, db=mock_db
            )

        assert result["model_id"] == str(model_id)
        assert model_mock.markup_rate == Decimal("0.4")

    @pytest.mark.asyncio
    async def test_sets_model_markup_to_null_for_inherit(self, mock_db, model_id):
        """Setting markup_rate to None means model inherits from provider/global."""
        model_mock = MagicMock()
        model_mock.id = model_id
        model_mock.model_id = "groq/llama-3.1-8b-instant"
        model_mock.display_name = "Llama 3.1 8B"
        model_mock.markup_rate = Decimal("0.30")
        model_mock.updated_at = None

        mock_db.execute.return_value = _make_scalar_result(model_mock)

        with patch("app.services.admin_markup.invalidate_markup_cache", new_callable=AsyncMock):
            result = await update_model_markup(
                model_id=model_id, markup_rate=None, db=mock_db
            )

        assert result["markup_rate"] is None
        assert model_mock.markup_rate is None

    @pytest.mark.asyncio
    async def test_invalidates_specific_model_cache(self, mock_db, model_id):
        """Model markup update invalidates only that model's cache."""
        model_mock = MagicMock()
        model_mock.id = model_id
        model_mock.model_id = "groq/llama-3.1-8b-instant"
        model_mock.display_name = "Llama 3.1 8B"
        model_mock.markup_rate = None
        model_mock.updated_at = None

        mock_db.execute.return_value = _make_scalar_result(model_mock)

        with patch(
            "app.services.admin_markup.invalidate_markup_cache", new_callable=AsyncMock
        ) as mock_invalidate:
            await update_model_markup(
                model_id=model_id, markup_rate=0.50, db=mock_db
            )

        mock_invalidate.assert_called_once_with(model_id=model_id)


# ── Tests: list_models_with_markup ────────────────────────────────────────────


class TestListModelsWithMarkup:
    """Tests for list_models_with_markup service function."""

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_models(self, mock_db):
        """Should return empty list when no active models exist."""
        mock_db.execute.side_effect = [
            _make_scalars_result([]),  # models query
            _make_scalars_result([]),  # markup_config query
        ]

        result = await list_models_with_markup(db=mock_db)

        assert result == []

    @pytest.mark.asyncio
    async def test_resolves_model_level_markup(self, mock_db):
        """Model with its own markup_rate should show source='model'."""
        provider_id = uuid.uuid4()
        model_id = uuid.uuid4()

        provider_mock = MagicMock()
        provider_mock.id = provider_id
        provider_mock.display_name = "Groq"

        model_mock = MagicMock()
        model_mock.id = model_id
        model_mock.model_id = "groq/llama-3.1-8b-instant"
        model_mock.display_name = "Llama 3.1 8B"
        model_mock.provider_id = provider_id
        model_mock.provider = provider_mock
        model_mock.input_price_per_1m = Decimal("0.05")
        model_mock.output_price_per_1m = Decimal("0.08")
        model_mock.markup_rate = Decimal("0.50")

        # Global config
        global_config = MagicMock()
        global_config.scope = "global"
        global_config.provider_id = None
        global_config.markup_rate = Decimal("0.20")

        mock_db.execute.side_effect = [
            _make_scalars_result([model_mock]),
            _make_scalars_result([global_config]),
        ]

        result = await list_models_with_markup(db=mock_db)

        assert len(result) == 1
        assert result[0]["markup_rate"] == 0.50
        assert result[0]["markup_source"] == "model"
        # effective_input_price = 0.05 * (1 + 0.50) = 0.075
        assert result[0]["effective_input_price"] == 0.075
        # effective_output_price = 0.08 * (1 + 0.50) = 0.12
        assert result[0]["effective_output_price"] == 0.12

    @pytest.mark.asyncio
    async def test_resolves_provider_level_markup(self, mock_db):
        """Model without markup_rate should inherit from provider config."""
        provider_id = uuid.uuid4()
        model_id = uuid.uuid4()

        provider_mock = MagicMock()
        provider_mock.id = provider_id
        provider_mock.display_name = "DeepSeek"

        model_mock = MagicMock()
        model_mock.id = model_id
        model_mock.model_id = "deepseek/deepseek-chat"
        model_mock.display_name = "DeepSeek Chat"
        model_mock.provider_id = provider_id
        model_mock.provider = provider_mock
        model_mock.input_price_per_1m = Decimal("0.14")
        model_mock.output_price_per_1m = Decimal("0.28")
        model_mock.markup_rate = None  # inherits

        # Provider config
        provider_config = MagicMock()
        provider_config.scope = "provider"
        provider_config.provider_id = provider_id
        provider_config.markup_rate = Decimal("0.30")

        # Global config
        global_config = MagicMock()
        global_config.scope = "global"
        global_config.provider_id = None
        global_config.markup_rate = Decimal("0.20")

        mock_db.execute.side_effect = [
            _make_scalars_result([model_mock]),
            _make_scalars_result([provider_config, global_config]),
        ]

        result = await list_models_with_markup(db=mock_db)

        assert len(result) == 1
        assert result[0]["markup_rate"] == 0.30
        assert result[0]["markup_source"] == "provider"

    @pytest.mark.asyncio
    async def test_resolves_global_level_markup(self, mock_db):
        """Model without markup_rate and no provider config uses global."""
        provider_id = uuid.uuid4()
        model_id = uuid.uuid4()

        provider_mock = MagicMock()
        provider_mock.id = provider_id
        provider_mock.display_name = "Gemini"

        model_mock = MagicMock()
        model_mock.id = model_id
        model_mock.model_id = "gemini/gemini-1.5-flash"
        model_mock.display_name = "Gemini Flash"
        model_mock.provider_id = provider_id
        model_mock.provider = provider_mock
        model_mock.input_price_per_1m = Decimal("0.075")
        model_mock.output_price_per_1m = Decimal("0.30")
        model_mock.markup_rate = None

        # Only global config
        global_config = MagicMock()
        global_config.scope = "global"
        global_config.provider_id = None
        global_config.markup_rate = Decimal("0.20")

        mock_db.execute.side_effect = [
            _make_scalars_result([model_mock]),
            _make_scalars_result([global_config]),
        ]

        result = await list_models_with_markup(db=mock_db)

        assert len(result) == 1
        assert result[0]["markup_rate"] == 0.20
        assert result[0]["markup_source"] == "global"
        # effective_input_price = 0.075 * 1.20 = 0.09
        assert result[0]["effective_input_price"] == 0.09
        # effective_output_price = 0.30 * 1.20 = 0.36
        assert result[0]["effective_output_price"] == 0.36

    @pytest.mark.asyncio
    async def test_returns_all_required_fields(self, mock_db):
        """Each model in the response should have all required fields."""
        provider_id = uuid.uuid4()
        model_id = uuid.uuid4()

        provider_mock = MagicMock()
        provider_mock.id = provider_id
        provider_mock.display_name = "Groq"

        model_mock = MagicMock()
        model_mock.id = model_id
        model_mock.model_id = "groq/llama-3.1-8b-instant"
        model_mock.display_name = "Llama 3.1 8B"
        model_mock.provider_id = provider_id
        model_mock.provider = provider_mock
        model_mock.input_price_per_1m = Decimal("0.05")
        model_mock.output_price_per_1m = Decimal("0.08")
        model_mock.markup_rate = Decimal("0.25")

        mock_db.execute.side_effect = [
            _make_scalars_result([model_mock]),
            _make_scalars_result([]),
        ]

        result = await list_models_with_markup(db=mock_db)

        required_fields = [
            "id", "model_id", "display_name", "provider_name",
            "input_price_per_1m", "output_price_per_1m",
            "markup_rate", "markup_source",
            "effective_input_price", "effective_output_price",
        ]
        for field in required_fields:
            assert field in result[0], f"Missing field: {field}"

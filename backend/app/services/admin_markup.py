"""
Admin markup management service.

Handles CRUD operations for markup configuration at global, provider, and model levels.
Follows the thin-router, fat-service pattern.

Requirement 4: AC1, AC5, AC6
Requirement 11: AC7
"""
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.logging_config import get_logger
from app.models.model import MarkupConfig, Model
from app.models.provider import Provider
from app.services.markup import invalidate_markup_cache

logger = get_logger(__name__)


# ── Public API ────────────────────────────────────────────────────────────────


async def update_global_markup(
    markup_rate: float,
    db: AsyncSession,
) -> dict[str, Any]:
    """
    Upsert the global default markup rate.

    Args:
        markup_rate: New global markup rate (0.0–5.0).
        db: Async database session.

    Returns:
        Dict with scope, markup_rate, and updated_at.
    """
    # Find existing global config
    result = await db.execute(
        select(MarkupConfig).where(MarkupConfig.scope == "global")
    )
    config = result.scalar_one_or_none()

    if config is None:
        # Create new global config row
        config = MarkupConfig(
            scope="global",
            provider_id=None,
            model_id=None,
            markup_rate=Decimal(str(markup_rate)),
        )
        db.add(config)
    else:
        config.markup_rate = Decimal(str(markup_rate))

    await db.flush()

    # Invalidate all markup caches (global change affects everything)
    await invalidate_markup_cache(model_id=None)

    logger.info(
        "global_markup_updated",
        markup_rate=markup_rate,
    )

    return {
        "scope": "global",
        "markup_rate": float(config.markup_rate),
        "updated_at": config.updated_at.isoformat() if config.updated_at else None,
    }


async def update_provider_markup(
    provider_id: uuid.UUID,
    markup_rate: float,
    db: AsyncSession,
) -> dict[str, Any]:
    """
    Upsert the provider-level markup rate in markup_config.

    Args:
        provider_id: UUID of the provider.
        markup_rate: New provider markup rate (0.0–5.0).
        db: Async database session.

    Returns:
        Dict with scope, provider_id, markup_rate, and updated_at.

    Raises:
        ValueError: If provider not found.
    """
    # Verify provider exists
    provider_result = await db.execute(
        select(Provider).where(Provider.id == provider_id)
    )
    provider = provider_result.scalar_one_or_none()
    if provider is None:
        raise ValueError(f"Provider not found: {provider_id}")

    # Find existing provider-level config
    result = await db.execute(
        select(MarkupConfig).where(
            MarkupConfig.scope == "provider",
            MarkupConfig.provider_id == provider_id,
        )
    )
    config = result.scalar_one_or_none()

    if config is None:
        # Create new provider config row
        config = MarkupConfig(
            scope="provider",
            provider_id=provider_id,
            model_id=None,
            markup_rate=Decimal(str(markup_rate)),
        )
        db.add(config)
    else:
        config.markup_rate = Decimal(str(markup_rate))

    await db.flush()

    # Invalidate all markup caches (provider change affects all models under it)
    await invalidate_markup_cache(model_id=None)

    logger.info(
        "provider_markup_updated",
        provider_id=str(provider_id),
        provider_name=provider.name,
        markup_rate=markup_rate,
    )

    return {
        "scope": "provider",
        "provider_id": str(provider_id),
        "provider_name": provider.name,
        "markup_rate": float(config.markup_rate),
        "updated_at": config.updated_at.isoformat() if config.updated_at else None,
    }


async def update_model_markup(
    model_id: uuid.UUID,
    markup_rate: float | None,
    db: AsyncSession,
) -> dict[str, Any]:
    """
    Update model.markup_rate directly. If null, model inherits from provider/global.

    Args:
        model_id: UUID of the model.
        markup_rate: New model markup rate (0.0–5.0), or None to inherit.
        db: Async database session.

    Returns:
        Dict with model_id, markup_rate, and updated_at.

    Raises:
        ValueError: If model not found.
    """
    # Find the model
    result = await db.execute(
        select(Model).where(Model.id == model_id)
    )
    model = result.scalar_one_or_none()
    if model is None:
        raise ValueError(f"Model not found: {model_id}")

    # Update model markup_rate directly
    model.markup_rate = Decimal(str(markup_rate)) if markup_rate is not None else None

    await db.flush()

    # Invalidate cache for this specific model
    await invalidate_markup_cache(model_id=model_id)

    logger.info(
        "model_markup_updated",
        model_id=str(model_id),
        model_name=model.model_id,
        markup_rate=markup_rate,
    )

    return {
        "model_id": str(model_id),
        "model_name": model.model_id,
        "display_name": model.display_name,
        "markup_rate": float(model.markup_rate) if model.markup_rate is not None else None,
        "updated_at": model.updated_at.isoformat() if model.updated_at else None,
    }


async def list_models_with_markup(
    db: AsyncSession,
) -> list[dict[str, Any]]:
    """
    Return all models with resolved markup rate, source level, base price,
    and effective sell price.

    Returns:
        List of dicts with model info, resolved markup, and effective prices.
    """
    # Fetch all active models with their providers
    result = await db.execute(
        select(Model)
        .options(selectinload(Model.provider))
        .where(Model.is_active == True)  # noqa: E712
        .order_by(Model.display_name)
    )
    models = result.scalars().all()

    # Fetch all markup configs for resolution
    config_result = await db.execute(select(MarkupConfig))
    configs = config_result.scalars().all()

    # Build lookup maps
    global_rate = Decimal(str(0.20))  # default fallback
    provider_rates: dict[uuid.UUID, Decimal] = {}

    for config in configs:
        if config.scope == "global":
            global_rate = config.markup_rate
        elif config.scope == "provider" and config.provider_id is not None:
            provider_rates[config.provider_id] = config.markup_rate

    # Resolve markup for each model
    models_list = []
    for model in models:
        # Determine markup rate and source
        if model.markup_rate is not None:
            resolved_rate = model.markup_rate
            markup_source = "model"
        elif model.provider_id in provider_rates:
            resolved_rate = provider_rates[model.provider_id]
            markup_source = "provider"
        else:
            resolved_rate = global_rate
            markup_source = "global"

        # Calculate effective prices
        multiplier = Decimal("1") + resolved_rate
        effective_input_price = model.input_price_per_1m * multiplier
        effective_output_price = model.output_price_per_1m * multiplier

        models_list.append({
            "id": str(model.id),
            "model_id": model.model_id,
            "display_name": model.display_name,
            "provider_name": model.provider.display_name if model.provider else "Unknown",
            "input_price_per_1m": float(model.input_price_per_1m),
            "output_price_per_1m": float(model.output_price_per_1m),
            "markup_rate": float(resolved_rate),
            "markup_source": markup_source,
            "effective_input_price": float(effective_input_price.quantize(Decimal("0.000001"))),
            "effective_output_price": float(effective_output_price.quantize(Decimal("0.000001"))),
        })

    return models_list

"""
Admin router — admin-only endpoints for markup and model management.

All routes under this router require admin role (JWT + role='admin').
Implemented in Task 14: markup configuration endpoints.
"""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.logging_config import get_logger
from app.middleware.auth import require_admin
from app.services.admin_markup import (
    list_models_with_markup,
    update_global_markup,
    update_model_markup,
    update_provider_markup,
)

logger = get_logger(__name__)

router = APIRouter(dependencies=[Depends(require_admin)])


# ── Request/Response Schemas ──────────────────────────────────────────────────


class GlobalMarkupRequest(BaseModel):
    markup_rate: float = Field(..., ge=0.0, le=5.0, description="Global markup rate (0.0–5.0)")


class ProviderMarkupRequest(BaseModel):
    markup_rate: float = Field(..., ge=0.0, le=5.0, description="Provider markup rate (0.0–5.0)")


class ModelMarkupRequest(BaseModel):
    markup_rate: Optional[float] = Field(
        None, ge=0.0, le=5.0, description="Model markup rate (0.0–5.0), null to inherit"
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.put("/markup/global")
async def set_global_markup(
    body: GlobalMarkupRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Update the global default markup rate.

    The global rate is used as fallback when no model-level or provider-level
    markup is configured.

    Requirement 4: AC1, AC5
    """
    result = await update_global_markup(
        markup_rate=body.markup_rate,
        db=db,
    )
    return result


@router.put("/providers/{provider_id}/markup")
async def set_provider_markup(
    provider_id: uuid.UUID,
    body: ProviderMarkupRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Set provider-level markup rate in markup_config.

    This rate applies to all models under the provider that don't have
    their own model-level markup set.

    Requirement 4: AC1, AC5
    """
    try:
        result = await update_provider_markup(
            provider_id=provider_id,
            markup_rate=body.markup_rate,
            db=db,
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provider not found: {provider_id}",
        )
    return result


@router.put("/models/{model_id}/markup")
async def set_model_markup(
    model_id: uuid.UUID,
    body: ModelMarkupRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Set model-level markup rate directly on the model.

    If markup_rate is null, the model inherits from provider-level or global.

    Requirement 4: AC1, AC5
    """
    try:
        result = await update_model_markup(
            model_id=model_id,
            markup_rate=body.markup_rate,
            db=db,
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Model not found: {model_id}",
        )
    return result


@router.get("/models")
async def get_models(
    db: AsyncSession = Depends(get_db),
):
    """
    List all active models with resolved markup rate, source level,
    base price, and effective sell price.

    Requirement 4: AC6, Requirement 11: AC7
    """
    models = await list_models_with_markup(db=db)
    return {"models": models}

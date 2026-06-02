"""
Virtual Key management router — /api/keys endpoints.

Endpoints:
- POST /api/keys — create a new virtual key (max 10 per user)
- GET  /api/keys — list all keys for the current user
- DELETE /api/keys/{id} — revoke a virtual key

All endpoints require JWT authentication.
Requirement 2 (AC1–AC7).
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.logging_config import get_logger
from app.middleware.auth import get_current_user
from app.models.user import User
from app.services.virtual_key import (
    create_virtual_key,
    list_virtual_keys,
    revoke_virtual_key,
)

logger = get_logger(__name__)

router = APIRouter()


# ── Pydantic Schemas ──────────────────────────────────────────────────────────


class CreateKeyRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = Field(None, max_length=500)
    rate_limit_rpm: int | None = Field(None, ge=1, le=10000)


class VirtualKeyResponse(BaseModel):
    id: str
    name: str
    description: str | None
    key_prefix: str
    is_active: bool
    rate_limit_rpm: int | None
    last_used_at: str | None
    total_requests: int
    total_tokens: int
    created_at: str
    revoked_at: str | None


class CreateKeyResponse(BaseModel):
    """Response for key creation — includes plaintext key (shown once only)."""
    id: str
    name: str
    description: str | None
    key_prefix: str
    key: str  # Plaintext key — shown only once
    is_active: bool
    rate_limit_rpm: int | None
    created_at: str


class KeyListResponse(BaseModel):
    keys: list[VirtualKeyResponse]
    total: int


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post("", response_model=CreateKeyResponse, status_code=status.HTTP_201_CREATED)
async def create_key(
    body: CreateKeyRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Create a new virtual key.

    Generates a cryptographically random key (ag-sk-{32 chars}),
    stores SHA-256 hash + prefix, returns plaintext once only.
    Enforces max 10 active keys per user.

    Requirement 2 (AC2, AC3, AC5).
    """
    try:
        virtual_key, plaintext_key = await create_virtual_key(
            db=db,
            user_id=current_user.id,
            name=body.name,
            description=body.description,
            rate_limit_rpm=body.rate_limit_rpm,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )

    return CreateKeyResponse(
        id=str(virtual_key.id),
        name=virtual_key.name,
        description=virtual_key.description,
        key_prefix=virtual_key.key_prefix,
        key=plaintext_key,
        is_active=virtual_key.is_active,
        rate_limit_rpm=virtual_key.rate_limit_rpm,
        created_at=virtual_key.created_at.isoformat(),
    )


@router.get("", response_model=KeyListResponse)
async def list_keys(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    List all virtual keys for the current user.

    Returns keys with prefix, name, status, last_used_at, total_requests.
    Requirement 2 (AC7).
    """
    keys = await list_virtual_keys(db=db, user_id=current_user.id)

    key_responses = [
        VirtualKeyResponse(
            id=str(k.id),
            name=k.name,
            description=k.description,
            key_prefix=k.key_prefix,
            is_active=k.is_active,
            rate_limit_rpm=k.rate_limit_rpm,
            last_used_at=k.last_used_at.isoformat() if k.last_used_at else None,
            total_requests=k.total_requests,
            total_tokens=k.total_tokens,
            created_at=k.created_at.isoformat(),
            revoked_at=k.revoked_at.isoformat() if k.revoked_at else None,
        )
        for k in keys
    ]

    return KeyListResponse(keys=key_responses, total=len(key_responses))


@router.delete("/{key_id}", status_code=status.HTTP_200_OK)
async def delete_key(
    key_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Revoke a virtual key.

    Sets is_active=false, revoked_at=now(), and invalidates Redis cache
    immediately so the key is rejected within seconds.

    Requirement 2 (AC4, AC6).
    """
    try:
        virtual_key = await revoke_virtual_key(
            db=db,
            user_id=current_user.id,
            key_id=key_id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )

    return {
        "message": "Key revoked successfully",
        "id": str(virtual_key.id),
        "revoked_at": virtual_key.revoked_at.isoformat(),
    }

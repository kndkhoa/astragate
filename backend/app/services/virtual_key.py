"""
Virtual Key service — CRUD operations, key generation, and hashing.

Handles:
- Key generation (ag-sk-{32 random chars})
- SHA-256 hashing for storage
- Max 10 keys per user enforcement
- Key revocation with Redis cache invalidation
- Default key creation on registration
"""
import hashlib
import secrets
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging_config import get_logger
from app.models.virtual_key import VirtualKey
from app.redis_client import get_redis

logger = get_logger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

KEY_PREFIX_FORMAT = "ag-sk-"
KEY_RANDOM_LENGTH = 32
MAX_KEYS_PER_USER = 10
REDIS_VK_PREFIX = "vk:"
REDIS_VK_TTL = 30  # seconds


# ── Key Generation Helpers ────────────────────────────────────────────────────


def generate_virtual_key() -> str:
    """
    Generate a cryptographically random virtual key.

    Format: ag-sk-{32 random alphanumeric chars}
    """
    random_part = secrets.token_hex(16)  # 32 hex chars
    return f"{KEY_PREFIX_FORMAT}{random_part}"


def hash_key(plaintext_key: str) -> str:
    """Compute SHA-256 hash of a plaintext key."""
    return hashlib.sha256(plaintext_key.encode()).hexdigest()


def extract_prefix(plaintext_key: str) -> str:
    """Extract the display prefix (first 8 chars) from a plaintext key."""
    return plaintext_key[:8]


# ── Service Functions ─────────────────────────────────────────────────────────


async def create_virtual_key(
    db: AsyncSession,
    user_id: uuid.UUID,
    name: str,
    description: str | None = None,
    rate_limit_rpm: int | None = None,
) -> tuple[VirtualKey, str]:
    """
    Create a new virtual key for a user.

    Returns:
        Tuple of (VirtualKey model, plaintext_key).
        The plaintext key is returned only once at creation time.

    Raises:
        ValueError: If user already has MAX_KEYS_PER_USER active keys.
    """
    # Enforce max 10 keys per user (count active keys only)
    count_result = await db.execute(
        select(func.count(VirtualKey.id)).where(
            VirtualKey.user_id == user_id,
            VirtualKey.is_active == True,  # noqa: E712
        )
    )
    active_count = count_result.scalar_one()

    if active_count >= MAX_KEYS_PER_USER:
        raise ValueError(
            f"Maximum of {MAX_KEYS_PER_USER} active keys per user reached"
        )

    # Generate key
    plaintext_key = generate_virtual_key()
    key_hash_value = hash_key(plaintext_key)
    key_prefix = extract_prefix(plaintext_key)

    # Create DB record
    virtual_key = VirtualKey(
        id=uuid.uuid4(),
        user_id=user_id,
        name=name,
        description=description,
        key_hash=key_hash_value,
        key_prefix=key_prefix,
        is_active=True,
        rate_limit_rpm=rate_limit_rpm,
        total_requests=0,
        total_tokens=0,
    )
    db.add(virtual_key)
    await db.flush()

    logger.info(
        "virtual_key_created",
        user_id=str(user_id),
        key_id=str(virtual_key.id),
        key_prefix=key_prefix,
        name=name,
    )

    return virtual_key, plaintext_key


async def list_virtual_keys(
    db: AsyncSession,
    user_id: uuid.UUID,
) -> list[VirtualKey]:
    """
    List all virtual keys for a user (active and revoked).

    Returns keys ordered by created_at descending.
    """
    result = await db.execute(
        select(VirtualKey)
        .where(VirtualKey.user_id == user_id)
        .order_by(VirtualKey.created_at.desc())
    )
    return list(result.scalars().all())


async def revoke_virtual_key(
    db: AsyncSession,
    user_id: uuid.UUID,
    key_id: uuid.UUID,
) -> VirtualKey:
    """
    Revoke a virtual key by setting is_active=False and revoked_at=now().

    Also invalidates the Redis cache for immediate effect.

    Raises:
        ValueError: If key not found or doesn't belong to user.
    """
    result = await db.execute(
        select(VirtualKey).where(
            VirtualKey.id == key_id,
            VirtualKey.user_id == user_id,
        )
    )
    virtual_key = result.scalar_one_or_none()

    if virtual_key is None:
        raise ValueError("Virtual key not found")

    if not virtual_key.is_active:
        raise ValueError("Virtual key is already revoked")

    # Revoke the key
    virtual_key.is_active = False
    virtual_key.revoked_at = datetime.now(timezone.utc)
    await db.flush()

    # Invalidate Redis cache immediately
    try:
        redis = get_redis()
        cache_key = f"{REDIS_VK_PREFIX}{virtual_key.key_hash}"
        await redis.delete(cache_key)
        logger.info(
            "virtual_key_cache_invalidated",
            key_id=str(key_id),
            cache_key=cache_key,
        )
    except Exception as exc:
        # Non-fatal: key is revoked in DB, cache will expire naturally
        logger.warning(
            "virtual_key_cache_invalidation_failed",
            key_id=str(key_id),
            error=str(exc),
        )

    logger.info(
        "virtual_key_revoked",
        user_id=str(user_id),
        key_id=str(key_id),
        key_prefix=virtual_key.key_prefix,
    )

    return virtual_key


async def create_default_key(
    db: AsyncSession,
    user_id: uuid.UUID,
) -> tuple[VirtualKey, str]:
    """
    Create the default virtual key for a newly registered user.

    Returns:
        Tuple of (VirtualKey model, plaintext_key).
    """
    return await create_virtual_key(
        db=db,
        user_id=user_id,
        name="Default Key",
        description="Auto-created default key",
    )

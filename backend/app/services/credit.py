"""
Credit pre-check and hold service.

Implements the credit hold/settle pattern to prevent overdrawing:
  1. BEFORE calling LiteLLM: estimate max cost, hold credit atomically
  2. AFTER LiteLLM responds: settle with actual cost, release hold
  3. ON error/timeout: release hold, restore credit

Uses PostgreSQL SELECT ... FOR UPDATE for atomicity and Redis for hold keys
with TTL as a secondary safety net.

Requirement 3: AC2, AC3, AC4, AC5
"""
import uuid
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging_config import get_logger
from app.models.credit import CreditAccount, CreditTransaction
from app.redis_client import get_redis

logger = get_logger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

REDIS_HOLD_PREFIX = "credit_hold:"
REDIS_HOLD_TTL = 60  # seconds — hold expires automatically as safety net


# ── Exceptions ────────────────────────────────────────────────────────────────


class InsufficientCreditError(Exception):
    """Raised when user's credit balance is insufficient for the estimated cost."""

    def __init__(self, balance: Decimal, required: Decimal):
        self.balance = balance
        self.required = required
        super().__init__(
            f"Insufficient credit: balance={balance}, required={required}"
        )


# ── Public API ────────────────────────────────────────────────────────────────


def estimate_max_cost(
    input_price_per_1m: Decimal,
    output_price_per_1m: Decimal,
    max_tokens: int,
    markup_rate: float,
) -> Decimal:
    """
    Estimate the maximum cost for a request based on max_tokens.

    The estimate uses the output price (which is typically higher) applied to
    max_tokens as a conservative upper bound. This ensures the hold covers
    the worst case.

    Formula: estimated_cost = (max_tokens / 1_000_000) × output_price_per_1M × (1 + markup_rate)

    Args:
        input_price_per_1m: Input token price per 1M tokens (USD).
        output_price_per_1m: Output token price per 1M tokens (USD).
        max_tokens: Maximum tokens the model may generate.
        markup_rate: The effective markup rate (e.g. 0.20 for 20%).

    Returns:
        Estimated maximum cost as a Decimal.
    """
    # Use output price as conservative estimate (output is typically more expensive)
    tokens_fraction = Decimal(str(max_tokens)) / Decimal("1000000")
    base_cost = tokens_fraction * output_price_per_1m
    markup_multiplier = Decimal("1") + Decimal(str(markup_rate))
    estimated_cost = base_cost * markup_multiplier

    logger.debug(
        "credit_estimate_max_cost",
        max_tokens=max_tokens,
        output_price_per_1m=str(output_price_per_1m),
        markup_rate=markup_rate,
        estimated_cost=str(estimated_cost),
    )

    return estimated_cost


async def hold_credit(
    user_id: uuid.UUID,
    request_id: str,
    amount: Decimal,
    db: AsyncSession,
) -> None:
    """
    Atomically check balance and hold credit for a request.

    Uses SELECT ... FOR UPDATE on credit_accounts to prevent race conditions
    when multiple concurrent requests try to deduct from the same account.

    Also stores the hold amount in Redis with TTL as a secondary safety net
    (auto-expires if settle/release is never called).

    Args:
        user_id: The user's UUID.
        request_id: Unique request identifier for this hold.
        amount: The estimated max cost to hold.
        db: Async database session (caller manages transaction).

    Raises:
        InsufficientCreditError: If balance < amount.
    """
    # 1. Lock the credit account row and check balance
    stmt = (
        select(CreditAccount)
        .where(CreditAccount.user_id == user_id)
        .with_for_update()
    )
    result = await db.execute(stmt)
    account = result.scalar_one_or_none()

    if account is None:
        logger.error(
            "credit_hold_no_account",
            user_id=str(user_id),
            request_id=request_id,
        )
        raise InsufficientCreditError(
            balance=Decimal("0"), required=amount
        )

    # 2. Check if balance is sufficient
    if account.balance_usd < amount:
        logger.warning(
            "credit_hold_insufficient",
            user_id=str(user_id),
            request_id=request_id,
            balance=str(account.balance_usd),
            required=str(amount),
        )
        raise InsufficientCreditError(
            balance=account.balance_usd, required=amount
        )

    # 3. Deduct hold amount from balance atomically
    account.balance_usd = account.balance_usd - amount
    await db.flush()

    # 4. Store hold in Redis with TTL (secondary safety net)
    try:
        redis = get_redis()
        hold_key = f"{REDIS_HOLD_PREFIX}{request_id}"
        hold_data = f"{user_id}:{amount}"
        await redis.set(hold_key, hold_data, ex=REDIS_HOLD_TTL)
    except Exception as exc:
        # Redis failure is non-fatal — PostgreSQL is the source of truth
        logger.warning(
            "credit_hold_redis_failed",
            user_id=str(user_id),
            request_id=request_id,
            error=str(exc),
        )

    logger.info(
        "credit_hold_success",
        user_id=str(user_id),
        request_id=request_id,
        amount=str(amount),
        balance_after=str(account.balance_usd),
    )


async def settle_credit(
    user_id: uuid.UUID,
    request_id: str,
    actual_cost: Decimal,
    usage_record_id: uuid.UUID | None = None,
    db: AsyncSession | None = None,
) -> Decimal:
    """
    Settle credit after a successful LLM response.

    The hold already deducted the estimated max cost. Settlement adjusts
    the balance to reflect the actual cost:
      - If actual_cost < estimated_hold: refund the difference
      - If actual_cost == estimated_hold: no adjustment needed
      - If actual_cost > estimated_hold: deduct the additional amount (rare edge case)

    Also inserts a credit_transactions record (type='usage') and releases
    the Redis hold key.

    Args:
        user_id: The user's UUID.
        request_id: The request identifier used during hold.
        actual_cost: The actual billed amount (base_cost × (1 + markup_rate)).
        usage_record_id: Optional UUID of the associated usage record.
        db: Async database session.

    Returns:
        The new balance after settlement.
    """
    if db is None:
        raise ValueError("db session is required for settle_credit")

    # 1. Get the hold amount from Redis
    held_amount = await _get_hold_amount(request_id)

    # 2. Lock the credit account and adjust balance
    stmt = (
        select(CreditAccount)
        .where(CreditAccount.user_id == user_id)
        .with_for_update()
    )
    result = await db.execute(stmt)
    account = result.scalar_one_or_none()

    if account is None:
        logger.error(
            "credit_settle_no_account",
            user_id=str(user_id),
            request_id=request_id,
        )
        # Release hold in Redis anyway
        await _release_redis_hold(request_id)
        return Decimal("0")

    # 3. Calculate adjustment
    # The hold already deducted `held_amount` from balance.
    # We need the net effect to be `actual_cost` deducted.
    # So we refund: held_amount - actual_cost (positive = refund to user)
    if held_amount is not None:
        adjustment = held_amount - actual_cost
        account.balance_usd = account.balance_usd + adjustment
    else:
        # No hold found (Redis expired or failed) — deduct actual cost directly
        account.balance_usd = account.balance_usd - actual_cost
        logger.warning(
            "credit_settle_no_hold_found",
            user_id=str(user_id),
            request_id=request_id,
            actual_cost=str(actual_cost),
        )

    await db.flush()

    # 4. Insert credit_transactions record
    transaction = CreditTransaction(
        user_id=user_id,
        type="usage",
        amount_usd=-actual_cost,  # negative = debit
        balance_after=account.balance_usd,
        usage_record_id=usage_record_id,
        description=f"API usage (request: {request_id})",
    )
    db.add(transaction)
    await db.flush()

    # 5. Release Redis hold
    await _release_redis_hold(request_id)

    logger.info(
        "credit_settle_success",
        user_id=str(user_id),
        request_id=request_id,
        actual_cost=str(actual_cost),
        held_amount=str(held_amount) if held_amount else "unknown",
        balance_after=str(account.balance_usd),
    )

    return account.balance_usd


async def release_hold(
    user_id: uuid.UUID,
    request_id: str,
    db: AsyncSession,
) -> None:
    """
    Release a credit hold on error or timeout — restore held amount to balance.

    Called when the LiteLLM request fails or times out, so the user
    should not be charged.

    Args:
        user_id: The user's UUID.
        request_id: The request identifier used during hold.
        db: Async database session.
    """
    # 1. Get the hold amount from Redis
    held_amount = await _get_hold_amount(request_id)

    if held_amount is None:
        logger.warning(
            "credit_release_no_hold_found",
            user_id=str(user_id),
            request_id=request_id,
        )
        # Nothing to release — hold may have already expired
        return

    # 2. Restore the held amount to the user's balance
    stmt = (
        select(CreditAccount)
        .where(CreditAccount.user_id == user_id)
        .with_for_update()
    )
    result = await db.execute(stmt)
    account = result.scalar_one_or_none()

    if account is None:
        logger.error(
            "credit_release_no_account",
            user_id=str(user_id),
            request_id=request_id,
        )
        await _release_redis_hold(request_id)
        return

    # 3. Add back the held amount
    account.balance_usd = account.balance_usd + held_amount
    await db.flush()

    # 4. Release Redis hold
    await _release_redis_hold(request_id)

    logger.info(
        "credit_release_success",
        user_id=str(user_id),
        request_id=request_id,
        released_amount=str(held_amount),
        balance_after=str(account.balance_usd),
    )


# ── Internal Helpers ──────────────────────────────────────────────────────────


async def _get_hold_amount(request_id: str) -> Decimal | None:
    """
    Retrieve the hold amount from Redis.

    Returns None if the hold key doesn't exist (expired or never set).
    """
    try:
        redis = get_redis()
        hold_key = f"{REDIS_HOLD_PREFIX}{request_id}"
        hold_data = await redis.get(hold_key)
        if hold_data is not None:
            # Format: "user_id:amount"
            _, amount_str = hold_data.rsplit(":", 1)
            return Decimal(amount_str)
    except Exception as exc:
        logger.warning(
            "credit_hold_redis_read_failed",
            request_id=request_id,
            error=str(exc),
        )
    return None


async def _release_redis_hold(request_id: str) -> None:
    """Delete the hold key from Redis."""
    try:
        redis = get_redis()
        hold_key = f"{REDIS_HOLD_PREFIX}{request_id}"
        await redis.delete(hold_key)
    except Exception as exc:
        # Non-fatal — key will expire via TTL anyway
        logger.warning(
            "credit_hold_redis_delete_failed",
            request_id=request_id,
            error=str(exc),
        )

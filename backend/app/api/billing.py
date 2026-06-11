"""
Billing router — Stripe Checkout, Webhook, and transaction history.
"""
import uuid
import stripe
from decimal import Decimal
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select, desc, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.logging_config import get_logger
from app.middleware.auth import get_current_user
from app.models.user import User
from app.models.credit import CreditAccount, CreditTransaction

logger = get_logger(__name__)

# Configure Stripe API Key
stripe.api_key = settings.STRIPE_SECRET_KEY

# Determine if we should run in Stripe Mock Mode (no key configured)
IS_MOCK_STRIPE = not settings.STRIPE_SECRET_KEY or settings.STRIPE_SECRET_KEY.startswith("change-me")

# Two routers:
# 1. Main router requiring JWT authentication for users
router = APIRouter(dependencies=[Depends(get_current_user)])
# 2. Public router for webhook (no JWT protection)
public_router = APIRouter()


# ── Request Schemas ───────────────────────────────────────────────────────────


class TopupRequest(BaseModel):
    amount: Decimal = Field(..., ge=Decimal("5.0"), description="Amount in USD to top up (min $5.00)")


# ── Authenticated Endpoints ───────────────────────────────────────────────────


@router.post("/topup")
async def create_topup_session(
    body: TopupRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """
    Create a Stripe Checkout Session for prepaid credits top-up.

    Requirement 3: AC6
    """
    amount = body.amount
    user_id = current_user.id
    
    # Log request headers to debug origin
    logger.info("topup_request_headers", headers=dict(request.headers))
    
    # Resolve origin dynamically from request headers
    origin = request.headers.get("origin") or request.headers.get("Origin") or "http://localhost:3000"
    success_url = f"{origin}/dashboard/billing?success=true"
    cancel_url = f"{origin}/dashboard/billing?cancel=true"

    if IS_MOCK_STRIPE:
        # Stripe Mock Mode — generate mock checkout url
        mock_session_id = f"mock_session_{uuid.uuid4()}"
        # Point success url directly to a mock top-up trigger, or let the user click a button on dashboard
        mock_checkout_url = (
            f"{origin}/dashboard/billing"
            f"?mock_stripe_session=true&amount={amount}&session_id={mock_session_id}"
        )
        logger.info(
            "stripe_mock_session_created",
            user_id=str(user_id),
            amount=str(amount),
            mock_session_id=mock_session_id,
        )
        return {
            "session_id": mock_session_id,
            "checkout_url": mock_checkout_url,
            "mock": True,
        }

    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[
                {
                    "price_data": {
                        "currency": "usd",
                        "product_data": {
                            "name": "AstraGate Prepaid Credits",
                            "description": f"Add ${amount:.2f} USD prepaid credits to account",
                        },
                        "unit_amount": int(amount * 100),  # cents
                    },
                    "quantity": 1,
                }
            ],
            mode="payment",
            payment_intent_data={
                "metadata": {
                    "user_id": str(user_id),
                    "amount": str(amount),
                }
            },
            success_url=success_url,
            cancel_url=cancel_url,
        )
        
        logger.info(
            "stripe_session_created",
            user_id=str(user_id),
            amount=str(amount),
            session_id=session.id,
        )
        return {
            "session_id": session.id,
            "checkout_url": session.url,
            "mock": False,
        }
    except Exception as exc:
        logger.error("stripe_session_creation_failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate Stripe Checkout Session",
        )


@router.get("/balance")
async def get_balance(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get current customer credit balance.

    Requirement 3: AC10 (part), Requirement 10: AC2
    """
    stmt = select(CreditAccount).where(CreditAccount.user_id == current_user.id)
    result = await db.execute(stmt)
    account = result.scalar_one_or_none()

    if not account:
        return {"balance_usd": 0.0}

    return {"balance_usd": float(account.balance_usd)}


@router.get("/transactions")
async def get_transactions(
    page: int = 1,
    limit: int = 20,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    List paginated credit transactions for current customer.

    Requirement 3: AC10, Requirement 10: AC7
    """
    offset = (page - 1) * limit
    
    # Query transactions
    stmt = (
        select(CreditTransaction)
        .where(CreditTransaction.user_id == current_user.id)
        .order_by(desc(CreditTransaction.created_at))
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(stmt)
    txs = result.scalars().all()
    
    # Query total count
    count_res = await db.execute(select(func.count()).select_from(CreditTransaction).where(CreditTransaction.user_id == current_user.id))
    total = count_res.scalar() or 0

    tx_list = []
    for tx in txs:
        tx_list.append({
            "id": tx.id,
            "type": tx.type,
            "amount_usd": float(tx.amount_usd),
            "balance_after": float(tx.balance_after),
            "stripe_payment_intent_id": tx.stripe_payment_intent_id,
            "description": tx.description,
            "created_at": tx.created_at,
        })

    return {
        "transactions": tx_list,
        "page": page,
        "limit": limit,
        "total": total,
    }


# ── Public Webhook Endpoint ───────────────────────────────────────────────────


@public_router.post("/webhook")
async def stripe_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Stripe Webhook handler to receive checkout/payment notifications.

    Requirement 3: AC7, AC8
    Requirement 14: AC6
    """
    payload = await request.body()
    sig_header = request.headers.get("Stripe-Signature")
    
    event = None

    if IS_MOCK_STRIPE:
        # Mock mode: Bypasses signature checking, parses body directly
        try:
            event = await request.json()
        except Exception as exc:
            logger.warning("stripe_mock_webhook_parsing_failed", error=str(exc))
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid mock event body",
            )
    else:
        # Production Mode: Verifies real Stripe Signature
        try:
            event = stripe.Webhook.construct_event(
                payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
            )
        except stripe.error.SignatureVerificationError as exc:
            logger.warning("stripe_webhook_signature_verification_failed", error=str(exc))
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid Stripe signature",
            )
        except Exception as exc:
            logger.error("stripe_webhook_general_error", error=str(exc))
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Webhook event error",
            )

    # Get event type and data object
    event_type = event.get("type") if isinstance(event, dict) else event.type
    event_data = event.get("data") if isinstance(event, dict) else event.data
    
    if event_type == "payment_intent.succeeded":
        payment_intent = event_data.get("object") if isinstance(event, dict) else event_data.object
        metadata = payment_intent.get("metadata", {})
        
        user_id_str = metadata.get("user_id")
        amount_str = metadata.get("amount")
        
        if user_id_str and amount_str:
            try:
                user_id = uuid.UUID(user_id_str)
                amount = Decimal(amount_str)
            except ValueError:
                logger.warning("stripe_webhook_invalid_metadata", user_id=user_id_str, amount=amount_str)
                return {"status": "ignored_invalid_metadata"}

            # Atomically lock and update credit balance
            stmt = select(CreditAccount).where(CreditAccount.user_id == user_id).with_for_update()
            res = await db.execute(stmt)
            account = res.scalar_one_or_none()

            if account:
                balance_before = account.balance_usd
                balance_after = balance_before + amount
                
                account.balance_usd = balance_after
                account.last_topup_amount = amount
                account.last_topup_at = datetime.now(timezone.utc)
                account.low_balance_alert_sent_at = None  # Reset alert flag
                
                # Write to transaction history
                pi_id = payment_intent.get("id") if isinstance(payment_intent, dict) else payment_intent.id
                tx = CreditTransaction(
                    user_id=user_id,
                    type="topup",
                    amount_usd=amount,
                    balance_after=balance_after,
                    stripe_payment_intent_id=pi_id,
                    description=f"Prepaid credit top-up (${amount:.2f})",
                )
                db.add(tx)
                
                await db.commit()
                
                logger.info(
                    "stripe_topup_completed",
                    user_id=user_id_str,
                    amount=amount_str,
                    new_balance=str(balance_after),
                )
                
                # Resend Email Trigger
                user_stmt = select(User).where(User.id == user_id)
                user_res = await db.execute(user_stmt)
                user = user_res.scalar_one_or_none()
                if user:
                    from app.services.email import send_payment_confirmation
                    try:
                        await send_payment_confirmation(user.email, amount, balance_after)
                    except Exception as email_exc:
                        logger.error("failed_to_send_payment_confirmation_email", user_id=user_id_str, error=str(email_exc))
            else:
                logger.error("stripe_topup_failed_no_account", user_id=user_id_str)
                raise HTTPException(status_code=404, detail="User credit account not found")

    return {"status": "success"}

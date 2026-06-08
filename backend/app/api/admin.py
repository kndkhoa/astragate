"""
Admin router — admin-only endpoints for markup, model, provider, guardrail, and customer management.

All routes under this router require admin role (JWT + role='admin').
"""
import uuid
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.logging_config import get_logger
from app.middleware.auth import require_admin
from app.models.provider import Provider
from app.models.usage import ProviderBalanceLog, UsageRecord
from app.models.guardrail import GuardrailKeyword, GuardrailEvent
from app.models.user import User
from app.models.credit import CreditAccount
from app.services.guardrail import invalidate_keyword_cache
from app.services.admin_markup import (
    list_models_with_markup,
    update_global_markup,
    update_model_markup,
    update_provider_markup,
)
from app.services.provider_balance import invalidate_provider_status_cache

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


class ProviderBalanceRequest(BaseModel):
    balance_usd: Decimal = Field(..., ge=Decimal("0.0"), description="New manual balance for the provider")
    note: Optional[str] = Field(None, description="Optional note for the manual update log")


class ProviderThresholdsRequest(BaseModel):
    warning_threshold: Decimal = Field(..., ge=Decimal("1.0"), description="Warning threshold (min $1)")
    hard_stop_threshold: Decimal = Field(..., ge=Decimal("1.0"), description="Hard stop threshold (min $1)")


class GuardrailKeywordCreate(BaseModel):
    keyword: str = Field(..., min_length=1, description="Banned keyword")
    scope: str = Field("both", pattern="^(input|output|both)$", description="Keyword scope ('input', 'output', 'both')")


class GuardrailKeywordUpdate(BaseModel):
    keyword: Optional[str] = Field(None, min_length=1, description="Banned keyword")
    scope: Optional[str] = Field(None, pattern="^(input|output|both)$", description="Keyword scope ('input', 'output', 'both')")


# ── Endpoints: Markup & Model Management (Task 14) ───────────────────────────


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


# ── Endpoints: Provider Management (Task 15) ──────────────────────────────────


@router.get("/providers")
async def get_providers(
    db: AsyncSession = Depends(get_db),
):
    """
    List all providers with details: balance, status, thresholds, burn rate, days remaining.

    Requirement 5: AC9
    Requirement 11: AC5
    """
    stmt = select(Provider)
    result = await db.execute(stmt)
    providers = result.scalars().all()

    twenty_four_hours_ago = datetime.now(timezone.utc) - timedelta(hours=24)

    provider_list = []
    for p in providers:
        # Compute burn rate: sum of base_cost_usd from usage_records in last 24h / 24
        burn_stmt = select(func.sum(UsageRecord.base_cost_usd)).where(
            UsageRecord.provider_id == p.id,
            UsageRecord.created_at >= twenty_four_hours_ago
        )
        burn_res = await db.execute(burn_stmt)
        total_cost_24h = burn_res.scalar() or Decimal("0.0")
        burn_rate = total_cost_24h / Decimal("24.0")

        # Compute days remaining
        days_remaining = None
        if burn_rate > 0:
            days_remaining = float(p.balance_usd / (burn_rate * Decimal("24.0")))

        provider_list.append({
            "id": p.id,
            "name": p.name,
            "display_name": p.display_name,
            "balance_usd": p.balance_usd,
            "warning_threshold": p.warning_threshold,
            "hard_stop_threshold": p.hard_stop_threshold,
            "status": p.status,
            "fallback_provider_id": p.fallback_provider_id,
            "burn_rate_hourly": burn_rate,
            "burn_rate_daily": burn_rate * Decimal("24.0"),
            "burn_rate_per_hour": burn_rate,
            "burn_rate_per_day": burn_rate * Decimal("24.0"),
            "days_remaining": days_remaining,
            "is_active": p.is_active,
            "hard_stop_activated_at": p.hard_stop_activated_at,
        })

    return {"providers": provider_list}


@router.put("/providers/{provider_id}/balance")
async def update_balance(
    provider_id: uuid.UUID,
    body: ProviderBalanceRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Manually update provider balance and write to provider_balance_log.

    Requirement 5: AC1, AC10
    """
    stmt = select(Provider).where(Provider.id == provider_id)
    result = await db.execute(stmt)
    provider = result.scalar_one_or_none()

    if not provider:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provider not found: {provider_id}",
        )

    balance_before = provider.balance_usd
    balance_after = body.balance_usd
    amount_change = balance_after - balance_before

    log_entry = ProviderBalanceLog(
        provider_id=provider.id,
        change_type="manual_update",
        amount_usd=amount_change,
        balance_before=balance_before,
        balance_after=balance_after,
        note=body.note or f"Manual balance update from {balance_before} to {balance_after}",
    )
    db.add(log_entry)
    provider.balance_usd = balance_after

    await db.flush()
    await invalidate_provider_status_cache(provider.id)

    logger.info(
        "provider_balance_manually_updated",
        provider_id=str(provider.id),
        balance_before=str(balance_before),
        balance_after=str(balance_after),
    )

    return {
        "id": provider.id,
        "name": provider.name,
        "balance_usd": provider.balance_usd,
        "status": provider.status,
    }


@router.put("/providers/{provider_id}/thresholds")
async def update_thresholds(
    provider_id: uuid.UUID,
    body: ProviderThresholdsRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Update provider's warning and hard stop thresholds.

    Requirement 5: AC3
    Requirement 11: AC6
    """
    stmt = select(Provider).where(Provider.id == provider_id)
    result = await db.execute(stmt)
    provider = result.scalar_one_or_none()

    if not provider:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provider not found: {provider_id}",
        )

    provider.warning_threshold = body.warning_threshold
    provider.hard_stop_threshold = body.hard_stop_threshold

    await db.flush()
    await invalidate_provider_status_cache(provider.id)

    logger.info(
        "provider_thresholds_updated",
        provider_id=str(provider.id),
        warning_threshold=str(body.warning_threshold),
        hard_stop_threshold=str(body.hard_stop_threshold),
    )

    return {
        "id": provider.id,
        "name": provider.name,
        "warning_threshold": provider.warning_threshold,
        "hard_stop_threshold": provider.hard_stop_threshold,
    }


@router.post("/providers/{provider_id}/release-hard-stop")
async def release_hard_stop(
    provider_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """
    Manually release Hard Stop on a provider.
    Can only be done if the current balance is >= hard_stop_threshold.

    Requirement 5: AC8
    """
    stmt = select(Provider).where(Provider.id == provider_id)
    result = await db.execute(stmt)
    provider = result.scalar_one_or_none()

    if not provider:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provider not found: {provider_id}",
        )

    if provider.balance_usd < provider.hard_stop_threshold:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Cannot release hard stop: provider balance (${provider.balance_usd:.4f}) "
                f"is still below hard stop threshold (${provider.hard_stop_threshold:.2f}). "
                "Please update the balance first."
            ),
        )

    # Re-evaluate status based on new balance
    if provider.balance_usd < provider.warning_threshold:
        provider.status = "warning"
    else:
        provider.status = "normal"

    provider.hard_stop_activated_at = None

    await db.flush()
    await invalidate_provider_status_cache(provider.id)

    logger.info(
        "provider_hard_stop_released",
        provider_id=str(provider.id),
        new_status=provider.status,
    )

    return {
        "id": provider.id,
        "name": provider.name,
        "status": provider.status,
        "hard_stop_activated_at": None,
    }


# ── Endpoints: Guardrails Management (Task 30) ───────────────────────────────


@router.get("/guardrails")
async def get_guardrail_keywords(
    db: AsyncSession = Depends(get_db),
):
    """
    List all active guardrail keywords and 7-day event count.
    """
    stmt = select(GuardrailKeyword).where(GuardrailKeyword.is_active == True).order_by(GuardrailKeyword.created_at.desc())
    res = await db.execute(stmt)
    keywords = res.scalars().all()
    
    seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)
    event_stmt = select(GuardrailEvent.keyword_matched, func.count(GuardrailEvent.id)).where(
        GuardrailEvent.created_at >= seven_days_ago
    ).group_by(GuardrailEvent.keyword_matched)
    event_res = await db.execute(event_stmt)
    event_counts = {row[0]: row[1] for row in event_res.all()}
    
    return {
        "keywords": [
            {
                "id": kw.id,
                "keyword": kw.keyword,
                "scope": kw.scope,
                "created_at": kw.created_at,
                "event_count_7d": event_counts.get(kw.keyword, 0)
            }
            for kw in keywords
        ],
        "total_events_7d": sum(event_counts.values())
    }


@router.post("/guardrails")
async def create_guardrail_keyword(
    body: GuardrailKeywordCreate,
    db: AsyncSession = Depends(get_db),
):
    """
    Add a new guardrail keyword.
    """
    stmt = select(GuardrailKeyword).where(
        GuardrailKeyword.keyword == body.keyword.strip(),
        GuardrailKeyword.is_active == True
    )
    res = await db.execute(stmt)
    existing = res.scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Banned keyword already exists: {body.keyword}"
        )
        
    keyword = GuardrailKeyword(
        keyword=body.keyword.strip(),
        scope=body.scope,
        is_active=True
    )
    db.add(keyword)
    await db.flush()
    
    await invalidate_keyword_cache()
    
    logger.info("guardrail_keyword_created", keyword=keyword.keyword, scope=keyword.scope)
    return keyword


@router.put("/guardrails/{keyword_id}")
async def update_guardrail_keyword(
    keyword_id: uuid.UUID,
    body: GuardrailKeywordUpdate,
    db: AsyncSession = Depends(get_db),
):
    """
    Update a guardrail keyword or its scope.
    """
    stmt = select(GuardrailKeyword).where(
        GuardrailKeyword.id == keyword_id,
        GuardrailKeyword.is_active == True
    )
    res = await db.execute(stmt)
    keyword = res.scalar_one_or_none()
    if not keyword:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Guardrail keyword not found: {keyword_id}"
        )
        
    if body.keyword is not None:
        keyword.keyword = body.keyword.strip()
    if body.scope is not None:
        keyword.scope = body.scope
        
    await db.flush()
    await invalidate_keyword_cache()
    
    logger.info("guardrail_keyword_updated", keyword_id=str(keyword.id), keyword=keyword.keyword, scope=keyword.scope)
    return keyword


@router.delete("/guardrails/{keyword_id}")
async def delete_guardrail_keyword(
    keyword_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """
    Soft delete a guardrail keyword.
    """
    stmt = select(GuardrailKeyword).where(
        GuardrailKeyword.id == keyword_id,
        GuardrailKeyword.is_active == True
    )
    res = await db.execute(stmt)
    keyword = res.scalar_one_or_none()
    if not keyword:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Guardrail keyword not found: {keyword_id}"
        )
        
    keyword.is_active = False
    await db.flush()
    await invalidate_keyword_cache()
    
    logger.info("guardrail_keyword_deleted", keyword_id=str(keyword_id))
    return {"status": "success", "id": keyword_id}


# ── Endpoints: Admin Analytics (Task 34) ─────────────────────────────────────


@router.get("/overview")
async def get_admin_overview(
    db: AsyncSession = Depends(get_db),
):
    """
    Get admin analytics overview.
    """
    # 1. Total customers (role = 'customer')
    customers_stmt = select(func.count(User.id)).where(User.role == "customer")
    customers_res = await db.execute(customers_stmt)
    total_customers = customers_res.scalar() or 0

    # 2. Today's revenue and requests
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    revenue_stmt = select(
        func.sum(UsageRecord.billed_amount_usd).label("revenue"),
        func.count(UsageRecord.id).label("requests")
    ).where(UsageRecord.created_at >= today_start)
    revenue_res = await db.execute(revenue_stmt)
    revenue_row = revenue_res.first()
    today_revenue = float(revenue_row[0]) if revenue_row and revenue_row[0] is not None else 0.0
    today_requests = int(revenue_row[1]) if revenue_row and revenue_row[1] is not None else 0

    # 3. Provider status summary
    providers_stmt = select(Provider)
    providers_res = await db.execute(providers_stmt)
    providers = providers_res.scalars().all()
    provider_summary = [
        {
            "id": p.id,
            "name": p.name,
            "display_name": p.display_name,
            "status": p.status,
            "balance_usd": float(p.balance_usd)
        }
        for p in providers
    ]

    # 4. Daily revenue and requests for last 30 days
    is_sqlite = db.bind.dialect.name == "sqlite"
    if is_sqlite:
        day_col = func.date(UsageRecord.created_at)
    else:
        day_col = func.date_trunc("day", UsageRecord.created_at)

    thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
    revenue_chart_stmt = (
        select(
            day_col.label("day"),
            func.sum(UsageRecord.billed_amount_usd).label("revenue"),
            func.count(UsageRecord.id).label("requests")
        )
        .where(UsageRecord.created_at >= thirty_days_ago)
        .group_by(day_col)
        .order_by(day_col.asc())
    )
    revenue_chart_res = await db.execute(revenue_chart_stmt)
    daily_revenue = []
    for row in revenue_chart_res.all():
        day_str = str(row[0])
        if isinstance(row[0], datetime):
            day_str = row[0].strftime("%Y-%m-%d")
        daily_revenue.append({
            "day": day_str,
            "revenue": float(row[1] or 0),
            "requests": int(row[2] or 0)
        })

    return {
        "total_customers": total_customers,
        "today_revenue": today_revenue,
        "today_requests": today_requests,
        "providers": provider_summary,
        "daily_revenue_30d": daily_revenue
    }


@router.get("/customers")
async def get_customers(
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """
    List customers with credit_balance, usage, and status.
    """
    thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
    
    # Subquery to aggregate last 30 days usage per user
    usage_sub = (
        select(
            UsageRecord.user_id,
            func.count(UsageRecord.id).label("total_requests"),
            func.sum(UsageRecord.billed_amount_usd).label("total_spend")
        )
        .where(UsageRecord.created_at >= thirty_days_ago)
        .group_by(UsageRecord.user_id)
        .subquery()
    )

    # Base query joining User, CreditAccount, and our usage subquery
    stmt = (
        select(
            User,
            CreditAccount.balance_usd,
            usage_sub.c.total_requests,
            usage_sub.c.total_spend
        )
        .outerjoin(CreditAccount, User.id == CreditAccount.user_id)
        .outerjoin(usage_sub, User.id == usage_sub.c.user_id)
        .where(User.role == "customer")
    )

    if search:
        stmt = stmt.where(User.email.ilike(f"%{search.strip()}%"))

    # Count total
    count_stmt = select(func.count()).select_from(stmt.subquery())
    count_res = await db.execute(count_stmt)
    total_count = count_res.scalar() or 0

    # Paginate and order
    stmt = stmt.order_by(User.created_at.desc()).limit(page_size).offset((page - 1) * page_size)
    res = await db.execute(stmt)
    rows = res.all()

    customers = []
    for user, balance, total_requests, total_spend in rows:
        customers.append({
            "id": user.id,
            "email": user.email,
            "created_at": user.created_at,
            "is_active": user.is_active,
            "credit_balance": float(balance) if balance is not None else 0.0,
            "total_requests_30d": total_requests or 0,
            "total_spend_30d": float(total_spend) if total_spend is not None else 0.0,
        })

    return {
        "customers": customers,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total_count": total_count,
            "total_pages": (total_count + page_size - 1) // page_size,
        }
    }


@router.get("/customers/{customer_id}")
async def get_customer_detail(
    customer_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """
    Get customer detail with paginated usage records.
    """
    # Fetch user
    user_stmt = select(User).where(User.id == customer_id, User.role == "customer")
    user_res = await db.execute(user_stmt)
    user = user_res.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Customer not found"
        )

    # Fetch credit account
    credit_stmt = select(CreditAccount).where(CreditAccount.user_id == customer_id)
    credit_res = await db.execute(credit_stmt)
    credit = credit_res.scalar_one_or_none()

    # Fetch paginated usage records
    usage_stmt = select(UsageRecord).where(UsageRecord.user_id == customer_id)
    
    # Count total usage records
    count_stmt = select(func.count()).select_from(usage_stmt.subquery())
    count_res = await db.execute(count_stmt)
    total_count = count_res.scalar() or 0

    # Paginate usage records
    usage_stmt = usage_stmt.order_by(UsageRecord.created_at.desc()).limit(page_size).offset((page - 1) * page_size)
    usage_res = await db.execute(usage_stmt)
    records = usage_res.scalars().all()

    return {
        "id": user.id,
        "email": user.email,
        "created_at": user.created_at,
        "is_active": user.is_active,
        "credit_balance": float(credit.balance_usd) if credit else 0.0,
        "last_topup_amount": float(credit.last_topup_amount) if credit and credit.last_topup_amount is not None else None,
        "last_topup_at": credit.last_topup_at if credit else None,
        "usage": {
            "records": [
                {
                    "id": r.id,
                    "timestamp": r.created_at,
                    "model_name": r.model_name,
                    "provider_name": r.provider_name,
                    "input_tokens": r.input_tokens,
                    "output_tokens": r.output_tokens,
                    "total_tokens": r.total_tokens,
                    "billed_amount_usd": float(r.billed_amount_usd),
                    "latency_ms": r.latency_ms,
                    "cache_hit": r.cache_hit,
                    "is_fallback": r.is_fallback,
                    "status": r.status,
                }
                for r in records
            ],
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total_count": total_count,
                "total_pages": (total_count + page_size - 1) // page_size,
            }
        }
    }


# ── Endpoints: Cache Toggle (Task 42) ────────────────────────────────────────


@router.post("/cache/toggle")
async def toggle_exact_cache(
    enabled: bool,
):
    """
    Toggle exact cache in litellm_config.yaml.
    """
    import os
    paths = [
        "../litellm/litellm_config.yaml",
        "litellm/litellm_config.yaml",
        "./litellm/litellm_config.yaml",
    ]
    config_path = None
    for p in paths:
        if os.path.exists(p):
            config_path = p
            break
            
    if not config_path:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        candidate = os.path.abspath(os.path.join(current_dir, "../../../litellm/litellm_config.yaml"))
        if os.path.exists(candidate):
            config_path = candidate

    if not config_path or not os.path.exists(config_path):
        raise HTTPException(status_code=500, detail="litellm_config.yaml not found")
        
    try:
        with open(config_path, "r") as f:
            lines = f.readlines()
            
        new_lines = []
        in_cache = False
        for line in lines:
            if line.strip().startswith("cache:"):
                in_cache = True
                new_lines.append(line)
                continue
            
            if in_cache:
                if line.strip() and not line.startswith(" ") and not line.startswith("#"):
                    in_cache = False
                elif "ttl:" in line:
                    if enabled:
                        line = "  ttl: 3600\n"
                    else:
                        line = "  ttl: 0\n"
            new_lines.append(line)
            
        with open(config_path, "w") as f:
            f.writelines(new_lines)
            
        return {"status": "success", "exact_cache_enabled": enabled}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to update litellm_config: {str(exc)}")

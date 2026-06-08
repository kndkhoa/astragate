"""
Usage analytics router.
Provides query and export endpoints for customer usage history.
"""
import csv
import io
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import case, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import get_current_user, require_admin
from app.models.usage import UsageRecord
from app.models.user import User
from app.models.virtual_key import VirtualKey

router = APIRouter()


# ── Response Schemas ──────────────────────────────────────────────────────────


class UsageRecordResponse(BaseModel):
    id: uuid.UUID
    timestamp: datetime
    model_name: str
    provider_name: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    billed_amount_usd: float
    latency_ms: Optional[int]
    cache_hit: bool
    is_fallback: bool
    status: str


class UsageSummaryItem(BaseModel):
    day: str
    total_requests: int
    total_tokens: int
    total_cost: float
    cache_hit_rate: float
    error_rate: float


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("", response_model=dict)
async def get_usage_records(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    virtual_key_id: Optional[uuid.UUID] = Query(None),
    model_name: Optional[str] = Query(None),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get paginated usage records for the authenticated user (Task 32).
    """
    stmt = select(UsageRecord).where(UsageRecord.user_id == current_user.id)

    if virtual_key_id:
        stmt = stmt.where(UsageRecord.virtual_key_id == virtual_key_id)
    if model_name:
        stmt = stmt.where(UsageRecord.model_name == model_name)
    if start_date:
        stmt = stmt.where(UsageRecord.created_at >= start_date)
    if end_date:
        stmt = stmt.where(UsageRecord.created_at <= end_date)

    # Count total
    count_stmt = select(func.count()).select_from(stmt.subquery())
    count_res = await db.execute(count_stmt)
    total_count = count_res.scalar() or 0

    # Paginate and fetch
    stmt = stmt.order_by(UsageRecord.created_at.desc()).limit(page_size).offset((page - 1) * page_size)
    res = await db.execute(stmt)
    records = res.scalars().all()

    return {
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
        },
    }


@router.get("/summary", response_model=List[UsageSummaryItem])
async def get_usage_summary(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get daily aggregated stats for the last 30 days (Task 32).
    """
    is_sqlite = db.bind.dialect.name == "sqlite"
    if is_sqlite:
        day_col = func.date(UsageRecord.created_at)
    else:
        day_col = func.date_trunc("day", UsageRecord.created_at)

    stmt = (
        select(
            day_col.label("day"),
            func.count(UsageRecord.id).label("total_requests"),
            func.sum(UsageRecord.total_tokens).label("total_tokens"),
            func.sum(UsageRecord.billed_amount_usd).label("total_cost"),
            func.sum(case((UsageRecord.cache_hit == True, 1), else_=0)).label("cache_hits"),
            func.sum(case((UsageRecord.status != "success", 1), else_=0)).label("errors"),
        )
        .where(
            UsageRecord.user_id == current_user.id,
            UsageRecord.created_at >= datetime.now(timezone.utc) - timedelta(days=30),
        )
        .group_by(day_col)
        .order_by(day_col.asc())
    )

    res = await db.execute(stmt)
    summary = []
    for row in res.all():
        day_str = str(row[0])
        if isinstance(row[0], datetime):
            day_str = row[0].strftime("%Y-%m-%d")
        total_reqs = row[1] or 0
        tot_tokens = row[2] or 0
        tot_cost = row[3] or Decimal("0")
        c_hits = row[4] or 0
        errs = row[5] or 0

        cache_hit_rate = (c_hits / total_reqs) if total_reqs > 0 else 0.0
        error_rate = (errs / total_reqs) if total_reqs > 0 else 0.0

        summary.append(
            UsageSummaryItem(
                day=day_str,
                total_requests=total_reqs,
                total_tokens=tot_tokens,
                total_cost=float(tot_cost),
                cache_hit_rate=cache_hit_rate,
                error_rate=error_rate,
            )
        )

    return summary


@router.get("/export")
async def export_usage_records(
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Stream CSV of usage records for date range (admin only) (Task 32 / 43).
    """
    async def csv_generator(db_session: AsyncSession, start: Optional[datetime], end: Optional[datetime]):
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "timestamp",
            "customer_email",
            "virtual_key_prefix",
            "model",
            "provider",
            "input_tokens",
            "output_tokens",
            "base_cost_usd",
            "markup_rate",
            "billed_amount_usd",
            "latency_ms",
            "cache_hit",
            "status",
        ])
        yield output.getvalue()
        output.seek(0)
        output.truncate(0)

        chunk_size = 500
        offset = 0
        while True:
            stmt = (
                select(UsageRecord, User.email, VirtualKey.key_prefix)
                .join(User, UsageRecord.user_id == User.id)
                .join(VirtualKey, UsageRecord.virtual_key_id == VirtualKey.id)
            )
            if start:
                stmt = stmt.where(UsageRecord.created_at >= start)
            if end:
                stmt = stmt.where(UsageRecord.created_at <= end)

            stmt = stmt.order_by(UsageRecord.created_at.desc()).limit(chunk_size).offset(offset)
            res = await db_session.execute(stmt)
            rows = res.all()
            if not rows:
                break

            for r, email, vk_prefix in rows:
                writer.writerow([
                    r.created_at.isoformat(),
                    email,
                    vk_prefix,
                    r.model_name,
                    r.provider_name,
                    r.input_tokens,
                    r.output_tokens,
                    float(r.base_cost_usd),
                    float(r.markup_rate),
                    float(r.billed_amount_usd),
                    r.latency_ms or "",
                    r.cache_hit,
                    r.status,
                ])
                yield output.getvalue()
                output.seek(0)
                output.truncate(0)

            offset += chunk_size

    return StreamingResponse(
        csv_generator(db, start_date, end_date),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=usage_export.csv"},
    )

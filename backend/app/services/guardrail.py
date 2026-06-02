"""
Guardrail service — keyword-based content filtering.

Scans request prompts (input) and LLM responses (output) against a
configurable list of banned keywords. Keywords are loaded from PostgreSQL
and cached in Redis for performance.

- Input guardrail: blocks the request with HTTP 400 before calling LiteLLM.
- Output guardrail (non-streaming only): replaces the response with an error message.

Keywords have scope: 'input' | 'output' | 'both'.
Violations are logged as guardrail_events with a truncated content snippet.

Requirement 8: AC1, AC2, AC3, AC4, AC6
"""
import json
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging_config import get_logger
from app.models.guardrail import GuardrailEvent, GuardrailKeyword
from app.redis_client import get_redis

logger = get_logger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

REDIS_GUARDRAIL_KEY = "guardrails:keywords"
REDIS_GUARDRAIL_TTL = 300  # seconds
CONTENT_SNIPPET_MAX_LENGTH = 100


# ── Data Classes ──────────────────────────────────────────────────────────────


@dataclass
class GuardrailResult:
    """Result of a guardrail check."""

    violated: bool
    keyword_matched: str | None = None
    content_snippet: str | None = None


# ── Public API ────────────────────────────────────────────────────────────────


async def check_input(text: str, db: AsyncSession) -> GuardrailResult:
    """
    Scan input text (prompt messages) against banned keywords with scope 'input' or 'both'.

    Args:
        text: The concatenated text content from all messages in the request body.
        db: Async database session.

    Returns:
        GuardrailResult indicating whether a violation was found.
    """
    keywords = await _get_keywords(db)
    input_keywords = [
        kw for kw in keywords if kw["scope"] in ("input", "both")
    ]
    return _scan_text(text, input_keywords)


async def check_output(text: str, db: AsyncSession) -> GuardrailResult:
    """
    Scan output text (LLM response content) against banned keywords with scope 'output' or 'both'.

    Args:
        text: The response content from LiteLLM.
        db: Async database session.

    Returns:
        GuardrailResult indicating whether a violation was found.
    """
    keywords = await _get_keywords(db)
    output_keywords = [
        kw for kw in keywords if kw["scope"] in ("output", "both")
    ]
    return _scan_text(text, output_keywords)


async def record_violation(
    result: GuardrailResult,
    direction: str,
    db: AsyncSession,
    virtual_key_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
) -> None:
    """
    Write a guardrail_events record for a detected violation.

    Args:
        result: The GuardrailResult from check_input or check_output.
        direction: 'input' or 'output'.
        db: Async database session.
        virtual_key_id: The Virtual Key used in the request (if available).
        user_id: The user who made the request (if available).
    """
    if not result.violated:
        return

    event = GuardrailEvent(
        virtual_key_id=virtual_key_id,
        user_id=user_id,
        direction=direction,
        keyword_matched=result.keyword_matched or "",
        content_snippet=result.content_snippet,
    )
    db.add(event)
    await db.flush()

    logger.warning(
        "guardrail_violation",
        direction=direction,
        keyword_matched=result.keyword_matched,
        content_snippet=result.content_snippet,
        virtual_key_id=str(virtual_key_id) if virtual_key_id else None,
        user_id=str(user_id) if user_id else None,
    )


async def invalidate_keyword_cache() -> None:
    """
    Invalidate the Redis cache for guardrail keywords.

    Call this after any keyword add/update/delete operation.
    """
    try:
        redis = get_redis()
        await redis.delete(REDIS_GUARDRAIL_KEY)
        logger.info("guardrail_cache_invalidated")
    except Exception as exc:
        # Non-fatal: cache will expire naturally after TTL
        logger.warning(
            "guardrail_cache_invalidation_failed",
            error=str(exc),
        )


# ── Internal Helpers ──────────────────────────────────────────────────────────


async def _get_keywords(db: AsyncSession) -> list[dict]:
    """
    Load active banned keywords, using Redis cache when available.

    Returns a list of dicts with 'keyword' and 'scope' keys.
    """
    # 1. Try Redis cache first
    cached = await _get_cached_keywords()
    if cached is not None:
        logger.debug("guardrail_cache_hit", keyword_count=len(cached))
        return cached

    # 2. Load from database
    keywords = await _load_keywords_from_db(db)

    # 3. Cache in Redis
    await _cache_keywords(keywords)

    logger.info(
        "guardrail_keywords_loaded",
        keyword_count=len(keywords),
        source="database",
    )

    return keywords


async def _get_cached_keywords() -> list[dict] | None:
    """Attempt to read keywords from Redis cache."""
    try:
        redis = get_redis()
        value = await redis.get(REDIS_GUARDRAIL_KEY)
        if value is not None:
            return json.loads(value)
    except Exception as exc:
        logger.warning(
            "guardrail_cache_read_failed",
            error=str(exc),
        )
    return None


async def _cache_keywords(keywords: list[dict]) -> None:
    """Store keywords in Redis with TTL."""
    try:
        redis = get_redis()
        await redis.set(
            REDIS_GUARDRAIL_KEY,
            json.dumps(keywords),
            ex=REDIS_GUARDRAIL_TTL,
        )
    except Exception as exc:
        # Non-fatal: next request will just query DB again
        logger.warning(
            "guardrail_cache_write_failed",
            error=str(exc),
        )


async def _load_keywords_from_db(db: AsyncSession) -> list[dict]:
    """Load all active keywords from the database."""
    stmt = select(GuardrailKeyword).where(GuardrailKeyword.is_active == True)  # noqa: E712
    result = await db.execute(stmt)
    rows = result.scalars().all()

    return [
        {"keyword": row.keyword, "scope": row.scope}
        for row in rows
    ]


def _scan_text(text: str, keywords: list[dict]) -> GuardrailResult:
    """
    Scan text for any matching keyword (case-insensitive).

    Returns a GuardrailResult with the first matched keyword and a
    truncated content snippet (first 100 chars) for logging.
    """
    if not text or not keywords:
        return GuardrailResult(violated=False)

    text_lower = text.lower()

    for kw_entry in keywords:
        keyword = kw_entry["keyword"]
        if keyword.lower() in text_lower:
            # Truncate content to first 100 chars for logging
            snippet = text[:CONTENT_SNIPPET_MAX_LENGTH]
            if len(text) > CONTENT_SNIPPET_MAX_LENGTH:
                snippet += "..."

            return GuardrailResult(
                violated=True,
                keyword_matched=keyword,
                content_snippet=snippet,
            )

    return GuardrailResult(violated=False)

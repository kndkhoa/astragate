"""
Gateway router — OpenAI-compatible proxy endpoints.

All /v1/* routes require Virtual Key authentication and are subject to the
per-key rate limit.
"""
import json
import time
import uuid
from typing import AsyncIterator
from decimal import Decimal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.logging_config import get_logger
from app.middleware.rate_limit import enforce_rate_limit
from app.models.virtual_key import VirtualKey
from app.models.model import Model
from app.services.credit import (
    hold_credit,
    release_hold,
    estimate_max_cost,
    InsufficientCreditError,
)
from app.services.guardrail import check_input, check_output, record_violation
from app.services.provider_balance import resolve_provider_for_request
from app.services.litellm_client import (
    get_client,
    LiteLLMTimeoutError,
    LiteLLMHTTPError,
)
from app.services.post_process import post_process_usage
from app.services.markup import resolve_markup_rate

logger = get_logger(__name__)

router = APIRouter(dependencies=[Depends(enforce_rate_limit)])

# ── Model Mappings ────────────────────────────────────────────────────────────

MODEL_NAME_TO_DB_ID = {
    "llama-3.1-8b": "groq/llama-3.1-8b-instant",
    "deepseek-chat": "deepseek/deepseek-chat",
    "gemini-flash": "gemini/gemini-1.5-flash",
}

DB_ID_TO_MODEL_NAME = {
    "groq/llama-3.1-8b-instant": "llama-3.1-8b",
    "deepseek/deepseek-chat": "deepseek-chat",
    "gemini/gemini-1.5-flash": "gemini-flash",
}


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/models")
async def get_models(
    db: AsyncSession = Depends(get_db),
):
    """
    List all active models exposed to customers.

    Requirement 13: AC3
    """
    stmt = (
        select(Model)
        .options(selectinload(Model.provider))
        .where(Model.is_active == True)  # noqa: E712
    )
    result = await db.execute(stmt)
    models = result.scalars().all()

    data = []
    for m in models:
        external_name = DB_ID_TO_MODEL_NAME.get(m.model_id, m.model_id)
        data.append(
            {
                "id": external_name,
                "object": "model",
                "created": int(m.created_at.timestamp()),
                "owned_by": m.provider.name if m.provider else "system",
            }
        )

    return {"object": "list", "data": data}


@router.post("/chat/completions")
async def chat_completions(
    body: dict,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    virtual_key: VirtualKey = Depends(enforce_rate_limit),
):
    """
    OpenAI-compatible chat completion proxy endpoint.

    Requirement 1: AC1, AC2, AC3, AC5, AC6
    Requirement 13: AC1, AC4, AC5, AC6, AC7
    """
    # Bind contextvars for structured logging
    structlog.contextvars.bind_contextvars(
        virtual_key_prefix=virtual_key.key_prefix,
        model=body.get("model"),
    )

    model_name = body.get("model")
    if not model_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing 'model' parameter in request body",
        )

    # 1. Look up Model in Database
    db_model_id = MODEL_NAME_TO_DB_ID.get(model_name, model_name)
    stmt = (
        select(Model)
        .where(Model.model_id == db_model_id, Model.is_active == True)  # noqa: E712
    )
    result = await db.execute(stmt)
    model = result.scalar_one_or_none()

    if not model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "model_not_found",
                    "message": f"The model '{model_name}' does not exist or is inactive.",
                    "type": "invalid_request_error",
                }
            },
        )

    # 2. Resolve Markup Rate
    markup_rate = await resolve_markup_rate(model.id, model.provider_id, db)

    # 3. Estimate Max Cost
    max_tokens = body.get("max_tokens", 2048)
    estimated_cost = estimate_max_cost(
        model.input_price_per_1m,
        model.output_price_per_1m,
        max_tokens,
        markup_rate,
    )

    # 4. Atomically Hold Credit
    user_id = virtual_key.user_id
    request_id = f"ag-req-{uuid.uuid4()}"

    try:
        await hold_credit(user_id, request_id, estimated_cost, db)
        # Commit the hold to release the SELECT FOR UPDATE lock and update balance before external call
        await db.commit()
    except InsufficientCreditError as exc:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Insufficient credit balance. Required estimate: ${estimated_cost:.6f}.",
        )

    # 5. Apply Input Guardrail
    messages = body.get("messages", [])
    input_text = ""
    for msg in messages:
        if isinstance(msg, dict) and "content" in msg:
            input_text += " " + str(msg["content"])

    guard_in_result = await check_input(input_text, db)
    if guard_in_result.violated:
        # Record violation and release hold
        await record_violation(
            guard_in_result,
            "input",
            db,
            virtual_key_id=virtual_key.id,
            user_id=user_id,
        )
        await release_hold(user_id, request_id, db)
        await db.commit()

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Request blocked by content policy. Matched keyword: {guard_in_result.keyword_matched}",
        )

    # 6. Provider status and Fallback checks
    routing_decision = await resolve_provider_for_request(model.provider_id, db)
    if routing_decision.should_block:
        await release_hold(user_id, request_id, db)
        await db.commit()

        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Upstream provider is currently unavailable (Hard Stop active) and no fallback is configured.",
        )

    actual_provider_id = routing_decision.provider.provider_id
    is_fallback = routing_decision.is_fallback

    # Rewrite model name if fallback routing happened
    if is_fallback:
        fallback_stmt = select(Model).where(
            Model.provider_id == actual_provider_id,
            Model.is_active == True,  # noqa: E712
        )
        fallback_result = await db.execute(fallback_stmt)
        fallback_model = fallback_result.scalars().first()
        if fallback_model:
            body["model"] = DB_ID_TO_MODEL_NAME.get(
                fallback_model.model_id, fallback_model.model_id
            )
            # Re-fetch model pricing for post processing
            model = fallback_model

    # 7. Call LiteLLM Proxy
    start_time = time.monotonic()
    is_stream = body.get("stream", False)
    client = get_client()

    if is_stream:
        # SSE Streaming handler
        async def stream_generator() -> AsyncIterator[bytes]:
            full_text = ""
            usage_dict = None
            try:
                async for chunk in client.stream_chat(body):
                    yield chunk

                    # Try to reconstruct usage and text content from stream chunks
                    try:
                        chunk_str = chunk.decode("utf-8", errors="ignore").strip()
                        for line in chunk_str.split("\n"):
                            if line.startswith("data:"):
                                data_str = line[5:].strip()
                                if data_str == "[DONE]":
                                    continue
                                data = json.loads(data_str)

                                choices = data.get("choices", [])
                                if choices:
                                    delta = choices[0].get("delta", {})
                                    if "content" in delta:
                                        full_text += delta["content"]

                                if "usage" in data and data["usage"]:
                                    usage_dict = data["usage"]
                    except Exception:
                        pass
            except Exception as exc:
                # Refund credit hold on failure
                logger.error(
                    "litellm_stream_failed",
                    request_id=request_id,
                    error=str(exc),
                )
                async with db.begin():
                    await release_hold(user_id, request_id, db)
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Stream processing failed mid-way.",
                )

            # Standardize usage dictionary if missing
            if not usage_dict:
                prompt_est = len(input_text) // 4
                comp_est = len(full_text) // 4
                usage_dict = {
                    "prompt_tokens": max(1, prompt_est),
                    "completion_tokens": max(1, comp_est),
                    "total_tokens": max(2, prompt_est + comp_est),
                }

            litellm_response = {
                "usage": usage_dict,
                "choices": [{"message": {"content": full_text}}],
            }

            # Enqueue post-processing task after stream complete
            background_tasks.add_task(
                post_process_usage,
                request_id,
                virtual_key.id,
                user_id,
                model.id,
                actual_provider_id,
                litellm_response,
                markup_rate,
                start_time,
                is_fallback=is_fallback,
            )

        return StreamingResponse(
            stream_generator(), media_type="text/event-stream"
        )

    else:
        # Non-streaming Handler
        try:
            response_dict = await client.post_chat(body)
        except LiteLLMTimeoutError as exc:
            await release_hold(user_id, request_id, db)
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail=str(exc),
            )
        except LiteLLMHTTPError as exc:
            await release_hold(user_id, request_id, db)
            await db.commit()
            raise HTTPException(
                status_code=exc.status_code,
                detail=f"Upstream provider error: {exc.body}",
            )
        except Exception as exc:
            await release_hold(user_id, request_id, db)
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"LiteLLM connection error: {str(exc)}",
            )

        # Apply Output Guardrail
        choices = response_dict.get("choices", [])
        output_text = ""
        if choices:
            message = choices[0].get("message", {})
            if "content" in message:
                output_text = message["content"]

        guard_out_result = await check_output(output_text, db)
        if guard_out_result.violated:
            await record_violation(
                guard_out_result,
                "output",
                db,
                virtual_key_id=virtual_key.id,
                user_id=user_id,
            )
            await release_hold(user_id, request_id, db)
            await db.commit()

            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Response blocked by content policy. Matched keyword: {guard_out_result.keyword_matched}",
            )

        # Enqueue post-processing task
        background_tasks.add_task(
            post_process_usage,
            request_id,
            virtual_key.id,
            user_id,
            model.id,
            actual_provider_id,
            response_dict,
            markup_rate,
            start_time,
            is_fallback=is_fallback,
        )

        return response_dict


@router.post("/embeddings")
async def embeddings(
    body: dict,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    virtual_key: VirtualKey = Depends(enforce_rate_limit),
):
    """
    OpenAI-compatible embeddings proxy endpoint.

    Requirement 1: AC1, AC2, AC3, AC6
    Requirement 13: AC2, AC4, AC6, AC7
    """
    # Bind contextvars for structured logging
    structlog.contextvars.bind_contextvars(
        virtual_key_prefix=virtual_key.key_prefix,
        model=body.get("model"),
    )

    model_name = body.get("model")
    if not model_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing 'model' parameter in request body",
        )

    # 1. Look up Model in Database
    db_model_id = MODEL_NAME_TO_DB_ID.get(model_name, model_name)
    stmt = (
        select(Model)
        .where(Model.model_id == db_model_id, Model.is_active == True)  # noqa: E712
    )
    result = await db.execute(stmt)
    model = result.scalar_one_or_none()

    if not model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "model_not_found",
                    "message": f"The model '{model_name}' does not exist or is inactive.",
                    "type": "invalid_request_error",
                }
            },
        )

    # 2. Resolve Markup Rate
    markup_rate = await resolve_markup_rate(model.id, model.provider_id, db)

    # 3. Estimate Max Cost based on input length
    input_val = body.get("input", "")
    input_text = ""
    if isinstance(input_val, str):
        input_text = input_val
    elif isinstance(input_val, list):
        input_text = " ".join([str(x) for x in input_val])

    # Simple estimation for input tokens
    max_tokens = max(1, len(input_text) // 4)

    estimated_cost = estimate_max_cost(
        model.input_price_per_1m,
        model.output_price_per_1m,
        max_tokens,
        markup_rate,
    )

    # 4. Hold Credit
    user_id = virtual_key.user_id
    request_id = f"ag-req-{uuid.uuid4()}"

    try:
        await hold_credit(user_id, request_id, estimated_cost, db)
        await db.commit()
    except InsufficientCreditError as exc:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Insufficient credit balance. Required estimate: ${estimated_cost:.6f}.",
        )

    # 5. Apply Input Guardrail
    guard_in_result = await check_input(input_text, db)
    if guard_in_result.violated:
        await record_violation(
            guard_in_result,
            "input",
            db,
            virtual_key_id=virtual_key.id,
            user_id=user_id,
        )
        await release_hold(user_id, request_id, db)
        await db.commit()

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Request blocked by content policy. Matched keyword: {guard_in_result.keyword_matched}",
        )

    # 6. Provider status and Fallback checks
    routing_decision = await resolve_provider_for_request(model.provider_id, db)
    if routing_decision.should_block:
        await release_hold(user_id, request_id, db)
        await db.commit()

        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Upstream provider is currently unavailable (Hard Stop active) and no fallback is configured.",
        )

    actual_provider_id = routing_decision.provider.provider_id
    is_fallback = routing_decision.is_fallback

    if is_fallback:
        fallback_stmt = select(Model).where(
            Model.provider_id == actual_provider_id,
            Model.is_active == True,  # noqa: E712
        )
        fallback_result = await db.execute(fallback_stmt)
        fallback_model = fallback_result.scalars().first()
        if fallback_model:
            body["model"] = DB_ID_TO_MODEL_NAME.get(
                fallback_model.model_id, fallback_model.model_id
            )
            model = fallback_model

    # 7. Call LiteLLM Embeddings
    start_time = time.monotonic()
    client = get_client()

    try:
        response_dict = await client.post_embeddings(body)
    except LiteLLMTimeoutError as exc:
        await release_hold(user_id, request_id, db)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=str(exc),
        )
    except LiteLLMHTTPError as exc:
        await release_hold(user_id, request_id, db)
        await db.commit()
        raise HTTPException(
            status_code=exc.status_code,
            detail=f"Upstream provider error: {exc.body}",
        )
    except Exception as exc:
        await release_hold(user_id, request_id, db)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"LiteLLM connection error: {str(exc)}",
        )

    # Enqueue post-processing task
    background_tasks.add_task(
        post_process_usage,
        request_id,
        virtual_key.id,
        user_id,
        model.id,
        actual_provider_id,
        response_dict,
        markup_rate,
        start_time,
        is_fallback=is_fallback,
    )

    return response_dict

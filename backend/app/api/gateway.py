"""
Gateway router — OpenAI-compatible proxy endpoints.

All /v1/* routes require Virtual Key authentication and are subject to the
per-key rate limit. `enforce_rate_limit` depends on `require_virtual_key`,
so a single router dependency covers both. Full pipeline implementation in
Task 23.
"""
from fastapi import APIRouter, Depends

from app.middleware.rate_limit import enforce_rate_limit

router = APIRouter(dependencies=[Depends(enforce_rate_limit)])


# Placeholder — full pipeline implemented in Task 23
# POST /v1/chat/completions
# POST /v1/embeddings
# GET  /v1/models

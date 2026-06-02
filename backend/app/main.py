"""
AstraGate API — Application factory.

Wires together:
- Lifespan (DB + Redis connect/disconnect)
- Routers (auth, gateway, billing, admin)
- Global exception handlers (HTTPException, RequestValidationError, Exception)
- Request logging middleware
- Health endpoint
"""
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

import httpx
import structlog
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import settings
from app.database import engine
from app.logging_config import configure_logging, get_logger
from app.redis_client import close_redis, get_redis, init_redis

# ── Routers ──────────────────────────────────────────────────────────────────
from app.api.auth import router as auth_router
from app.api.gateway import router as gateway_router
from app.api.billing import router as billing_router
from app.api.admin import router as admin_router
from app.api.keys import router as keys_router

# Configure structured JSON logging before anything else
configure_logging(settings.LOG_LEVEL)
logger = get_logger(__name__)


# ── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and shutdown."""
    # Startup
    logger.info("Starting AstraGate API", env=settings.APP_ENV)

    # Connect to Redis
    try:
        await init_redis()
        logger.info("Redis connected", url=settings.REDIS_URL)
    except Exception as exc:
        logger.error("Redis connection failed", error=str(exc))
        # Non-fatal on startup — health endpoint will report degraded

    # Verify DB connectivity (engine uses lazy connect; just log intent)
    logger.info("Database engine initialized", url=settings.DATABASE_URL.split("@")[-1])

    yield

    # Shutdown
    logger.info("Shutting down AstraGate API")
    await close_redis()
    await engine.dispose()
    logger.info("Shutdown complete")


# ── Application factory ───────────────────────────────────────────────────────

def create_app() -> FastAPI:
    application = FastAPI(
        title="AstraGate API",
        description="LLM API Gateway — OpenAI-compatible",
        version="0.1.0",
        lifespan=lifespan,
        # Disable default /docs redirect so we control it later
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    # ── Middleware: request logging ───────────────────────────────────────────
    @application.middleware("http")
    async def request_logging_middleware(request: Request, call_next):
        request_id = str(uuid.uuid4())
        # Bind request_id to structlog context for this request
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        start_time = time.perf_counter()
        response = None
        try:
            response = await call_next(request)
            return response
        finally:
            latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
            status_code = response.status_code if response is not None else 500
            logger.info(
                "request",
                method=request.method,
                path=request.url.path,
                status_code=status_code,
                latency_ms=latency_ms,
                request_id=request_id,
            )

    # ── Global exception handlers ─────────────────────────────────────────────

    @application.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        logger.warning(
            "http_exception",
            status_code=exc.status_code,
            detail=exc.detail,
            path=request.url.path,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": _http_status_to_code(exc.status_code),
                    "message": exc.detail,
                    "type": "api_error",
                }
            },
        )

    @application.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        logger.warning(
            "validation_error",
            errors=exc.errors(),
            path=request.url.path,
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "Request validation failed",
                    "type": "invalid_request_error",
                    "details": exc.errors(),
                }
            },
        )

    @application.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.error(
            "unhandled_exception",
            error=str(exc),
            exc_info=True,
            path=request.url.path,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": {
                    "code": "internal_server_error",
                    "message": "An unexpected error occurred",
                    "type": "api_error",
                }
            },
        )

    # ── Health endpoint ───────────────────────────────────────────────────────

    @application.get("/health", tags=["Health"])
    async def health_check() -> JSONResponse:
        """
        Check the health of all dependencies.

        Returns HTTP 200 if all dependencies are healthy,
        HTTP 503 if any dependency is down (Requirement 15 AC5, AC7).
        """
        results: dict[str, Any] = {
            "postgres": "error",
            "redis": "error",
            "litellm": "error",
        }

        # Check PostgreSQL
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            results["postgres"] = "ok"
        except Exception as exc:
            logger.error("health_check_postgres_failed", error=str(exc))

        # Check Redis
        try:
            redis = get_redis()
            await redis.ping()
            results["redis"] = "ok"
        except Exception as exc:
            logger.error("health_check_redis_failed", error=str(exc))

        # Check LiteLLM Proxy
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{settings.LITELLM_URL}/health")
                if resp.status_code < 500:
                    results["litellm"] = "ok"
                else:
                    logger.warning(
                        "health_check_litellm_degraded",
                        status_code=resp.status_code,
                    )
        except Exception as exc:
            logger.error("health_check_litellm_failed", error=str(exc))

        all_ok = all(v == "ok" for v in results.values())
        overall_status = "ok" if all_ok else "degraded"
        http_status = status.HTTP_200_OK if all_ok else status.HTTP_503_SERVICE_UNAVAILABLE

        return JSONResponse(
            status_code=http_status,
            content={"status": overall_status, **results},
        )

    # ── Routers ───────────────────────────────────────────────────────────────
    application.include_router(auth_router, prefix="/auth", tags=["Auth"])
    application.include_router(gateway_router, prefix="/v1", tags=["Gateway"])
    application.include_router(billing_router, prefix="/api/billing", tags=["Billing"])
    application.include_router(keys_router, prefix="/api/keys", tags=["Keys"])
    application.include_router(admin_router, prefix="/admin", tags=["Admin"])

    return application


def _http_status_to_code(status_code: int) -> str:
    """Map HTTP status code to a short error code string."""
    mapping = {
        400: "bad_request",
        401: "unauthorized",
        402: "payment_required",
        403: "forbidden",
        404: "not_found",
        409: "conflict",
        422: "validation_error",
        429: "rate_limit_exceeded",
        500: "internal_server_error",
        502: "bad_gateway",
        503: "service_unavailable",
        504: "gateway_timeout",
    }
    return mapping.get(status_code, f"http_{status_code}")


app = create_app()

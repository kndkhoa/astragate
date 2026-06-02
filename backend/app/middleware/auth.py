"""
JWT authentication and role-based access control dependencies.

Provides FastAPI Depends()-compatible callables for route-level protection:
- get_current_user: extracts Bearer token, decodes JWT, fetches user from DB
- require_authenticated: alias for get_current_user (semantic clarity)
- require_admin: depends on get_current_user, rejects non-admin with HTTP 403

Usage in routers:
    from app.middleware.auth import get_current_user, require_admin

    @router.get("/protected")
    async def protected_route(user: User = Depends(get_current_user)):
        ...

    @router.get("/admin-only")
    async def admin_route(user: User = Depends(require_admin)):
        ...

Router-level protection (applied to all routes in a router):
    router = APIRouter(dependencies=[Depends(get_current_user)])
    admin_router = APIRouter(dependencies=[Depends(require_admin)])
"""
from fastapi import Depends, HTTPException, Request, status

from app.api.auth import get_current_user  # noqa: F401 — re-exported
from app.logging_config import get_logger
from app.models.user import User

logger = get_logger(__name__)


async def require_authenticated(
    request: Request,
    current_user: User = Depends(get_current_user),
) -> User:
    """
    Dependency that ensures the request has a valid JWT and an active user.

    Functionally identical to get_current_user but provides semantic clarity
    when used as a route dependency for protected endpoints.
    Attaches current_user to request state for downstream access.
    """
    # Attach current_user to request state so other middleware/handlers can access it
    request.state.current_user = current_user
    return current_user


async def require_admin(
    request: Request,
    current_user: User = Depends(get_current_user),
) -> User:
    """
    Dependency that ensures the authenticated user has the 'admin' role.

    Raises HTTP 403 Forbidden if the user is not an admin.
    Use this on all /admin/* route handlers.
    """
    if current_user.role != "admin":
        logger.warning(
            "admin_access_denied",
            user_id=str(current_user.id),
            email=current_user.email,
            path=request.url.path,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    # Attach current_user to request state
    request.state.current_user = current_user
    return current_user

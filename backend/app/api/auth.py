"""
Auth router — registration, login, logout, OAuth, and current user info.

Endpoints:
- POST /auth/register — email/password signup, creates user + credit_account
- POST /auth/login — verify credentials, return JWT pair
- POST /auth/oauth/google — Google OAuth login/signup via ID token
- POST /auth/logout — invalidate refresh token
- GET  /auth/me — return current user info

Brute-force protection:
- Track failed_login_attempts in DB
- Lock account for 15 min after 10 consecutive failures (Redis counter)
"""
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import bcrypt
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, status
from jose import JWTError, jwt
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.logging_config import get_logger
from app.models.credit import CreditAccount, CreditTransaction
from app.models.user import User
from app.services.email import send_welcome
from app.redis_client import get_redis
from app.services.virtual_key import create_default_key

logger = get_logger(__name__)

router = APIRouter()

# ── Constants ─────────────────────────────────────────────────────────────────

BCRYPT_COST = 12
MAX_FAILED_ATTEMPTS = 10
LOCKOUT_MINUTES = 15
REDIS_LOGIN_ATTEMPTS_PREFIX = "login_attempts:"
REDIS_REFRESH_BLACKLIST_PREFIX = "refresh_blacklist:"


# ── Pydantic Schemas ──────────────────────────────────────────────────────────


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1)


class LogoutRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    default_key: str | None = None  # Plaintext key shown once on registration


class UserResponse(BaseModel):
    id: str
    email: str
    role: str
    is_active: bool
    created_at: str


class GoogleOAuthRequest(BaseModel):
    id_token: str = Field(..., min_length=1)


# ── JWT Helpers ───────────────────────────────────────────────────────────────


def _create_access_token(user_id: str, role: str) -> str:
    """Create a short-lived access token (15 min)."""
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload = {
        "sub": user_id,
        "role": role,
        "type": "access",
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def _create_refresh_token(user_id: str) -> str:
    """Create a long-lived refresh token (7 days)."""
    expire = datetime.now(timezone.utc) + timedelta(
        days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS
    )
    payload = {
        "sub": user_id,
        "type": "refresh",
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(
        payload, settings.JWT_REFRESH_SECRET, algorithm=settings.JWT_ALGORITHM
    )


def _hash_password(password: str) -> str:
    """Hash a password with bcrypt (cost=12)."""
    salt = bcrypt.gensalt(rounds=BCRYPT_COST)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def _verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against its bcrypt hash."""
    return bcrypt.checkpw(
        password.encode("utf-8"), password_hash.encode("utf-8")
    )


def _decode_access_token(token: str) -> dict[str, Any]:
    """Decode and validate an access token."""
    try:
        payload = jwt.decode(
            token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM]
        )
        if payload.get("type") != "access":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token type",
            )
        return payload
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )


# ── Dependencies ──────────────────────────────────────────────────────────────


async def _get_token_from_header(
    authorization: str = Header(..., alias="Authorization"),
) -> str:
    """Extract Bearer token from Authorization header."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header format",
        )
    return authorization[7:]


async def get_current_user(
    db: AsyncSession = Depends(get_db),
    token: str = Depends(_get_token_from_header),
) -> User:
    """Dependency to get the current authenticated user from JWT."""
    payload = _decode_access_token(token)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )

    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )
    return user


# ── Brute-force Protection ────────────────────────────────────────────────────


async def _check_account_lock(user: User) -> None:
    """Check if the account is locked due to too many failed login attempts."""
    if user.locked_until and user.locked_until > datetime.now(timezone.utc):
        remaining = (user.locked_until - datetime.now(timezone.utc)).seconds
        logger.warning(
            "login_attempt_on_locked_account",
            user_id=str(user.id),
            locked_until=user.locked_until.isoformat(),
        )
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail=f"Account is locked. Try again in {remaining} seconds.",
        )


async def _record_failed_login(user: User, db: AsyncSession) -> None:
    """Record a failed login attempt. Lock account after 10 failures."""
    redis = get_redis()
    redis_key = f"{REDIS_LOGIN_ATTEMPTS_PREFIX}{user.id}"

    # Increment Redis counter (TTL 15 min)
    attempts = await redis.incr(redis_key)
    if attempts == 1:
        await redis.expire(redis_key, LOCKOUT_MINUTES * 60)

    # Update DB counter
    user.failed_login_attempts = attempts

    if attempts >= MAX_FAILED_ATTEMPTS:
        user.locked_until = datetime.now(timezone.utc) + timedelta(
            minutes=LOCKOUT_MINUTES
        )
        logger.warning(
            "account_locked",
            user_id=str(user.id),
            failed_attempts=attempts,
            locked_until=user.locked_until.isoformat(),
        )

    await db.flush()


async def _reset_failed_login(user: User, db: AsyncSession) -> None:
    """Reset failed login counter on successful login."""
    redis = get_redis()
    redis_key = f"{REDIS_LOGIN_ATTEMPTS_PREFIX}{user.id}"
    await redis.delete(redis_key)

    user.failed_login_attempts = 0
    user.locked_until = None
    await db.flush()


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    """
    Register a new user with email and password.

    Creates user + credit_account (balance=$0), returns JWT pair.
    Requirement 9 (AC1), 14 (AC2).
    """
    # Check if email already exists
    result = await db.execute(select(User).where(User.email == body.email))
    existing_user = result.scalar_one_or_none()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    # Hash password with bcrypt (cost=12)
    password_hash = _hash_password(body.password)

    # Create user
    user = User(
        id=uuid.uuid4(),
        email=body.email,
        password_hash=password_hash,
        role="customer",
        is_active=True,
        failed_login_attempts=0,
    )
    db.add(user)
    await db.flush()  # Get user.id assigned

    # Create credit account with $1.00 balance (free credit)
    credit_account = CreditAccount(
        id=uuid.uuid4(),
        user_id=user.id,
        balance_usd=Decimal("1.0"),
    )
    db.add(credit_account)
    
    # Record the free credit transaction
    transaction = CreditTransaction(
        id=uuid.uuid4(),
        user_id=user.id,
        type="free_credit",
        amount_usd=Decimal("1.0"),
        balance_after=Decimal("1.0"),
        description="Signup free credit"
    )
    db.add(transaction)
    await db.flush()

    # Auto-create default Virtual Key (Requirement 2 AC1)
    _, default_key_plaintext = await create_default_key(db=db, user_id=user.id)
    
    # Schedule welcome email
    background_tasks.add_task(send_welcome, user.email, default_key_plaintext[:8])

    logger.info(
        "user_registered",
        user_id=str(user.id),
        email=user.email,
    )

    # Generate JWT pair
    access_token = _create_access_token(str(user.id), user.role)
    refresh_token = _create_refresh_token(str(user.id))

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        default_key=default_key_plaintext,
    )


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    """
    Login with email and password.

    Verifies password, checks account lock, returns access token (15min) + refresh token (7 days).
    Requirement 9 (AC1), 14 (AC5).
    """
    # Find user by email
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    if user is None:
        # Don't reveal whether email exists
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    # Check if account is locked
    await _check_account_lock(user)

    # Verify password (user must have a password_hash for email/password login)
    if not user.password_hash:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not _verify_password(body.password, user.password_hash):
        await _record_failed_login(user, db)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    # Check if user is active
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account is deactivated",
        )

    # Reset failed login counter on success
    await _reset_failed_login(user, db)

    logger.info(
        "user_logged_in",
        user_id=str(user.id),
        email=user.email,
    )

    # Generate JWT pair
    access_token = _create_access_token(str(user.id), user.role)
    refresh_token = _create_refresh_token(str(user.id))

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
    )


@router.post("/oauth/google", response_model=TokenResponse)
async def oauth_google(body: GoogleOAuthRequest, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    """
    Google OAuth login/signup.

    Verifies the Google ID token, upserts user with oauth_provider='google',
    and returns a JWT pair. If the email already exists with password auth,
    links the Google account to the existing user.
    Requirement 9 (AC1).
    """
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token as google_id_token

    # Verify the Google ID token
    try:
        id_info = google_id_token.verify_oauth2_token(
            body.id_token,
            google_requests.Request(),
            settings.GOOGLE_CLIENT_ID,
        )
    except ValueError as e:
        logger.warning("google_oauth_token_invalid", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Google ID token",
        )

    # Extract user info from verified token
    google_email: str | None = id_info.get("email")
    google_sub: str | None = id_info.get("sub")

    if not google_email or not google_sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Google token missing email or sub claim",
        )

    if not id_info.get("email_verified", False):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Google email not verified",
        )

    # Check if user already exists
    result = await db.execute(select(User).where(User.email == google_email))
    existing_user = result.scalar_one_or_none()

    if existing_user:
        # Link Google OAuth to existing account (handles password-auth users)
        if not existing_user.oauth_provider:
            existing_user.oauth_provider = "google"
            existing_user.oauth_sub = google_sub
            await db.flush()
            logger.info(
                "google_oauth_account_linked",
                user_id=str(existing_user.id),
                email=existing_user.email,
            )
        elif existing_user.oauth_provider == "google" and existing_user.oauth_sub != google_sub:
            # Same email, same provider but different sub — shouldn't happen, reject
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Account mismatch",
            )

        # Check if user is active
        if not existing_user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Account is deactivated",
            )

        logger.info(
            "google_oauth_login",
            user_id=str(existing_user.id),
            email=existing_user.email,
        )

        access_token = _create_access_token(str(existing_user.id), existing_user.role)
        refresh_token = _create_refresh_token(str(existing_user.id))

        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
        )

    # New user — create account with Google OAuth
    user = User(
        id=uuid.uuid4(),
        email=google_email,
        password_hash=None,
        oauth_provider="google",
        oauth_sub=google_sub,
        role="customer",
        is_active=True,
        failed_login_attempts=0,
    )
    db.add(user)
    await db.flush()

    # Create credit account with $1.00 balance (free credit)
    credit_account = CreditAccount(
        id=uuid.uuid4(),
        user_id=user.id,
        balance_usd=Decimal("1.0"),
    )
    db.add(credit_account)
    
    # Record the free credit transaction
    transaction = CreditTransaction(
        id=uuid.uuid4(),
        user_id=user.id,
        type="free_credit",
        amount_usd=Decimal("1.0"),
        balance_after=Decimal("1.0"),
        description="Signup free credit"
    )
    db.add(transaction)
    await db.flush()

    # Auto-create default Virtual Key (Requirement 2 AC1)
    _, default_key_plaintext = await create_default_key(db=db, user_id=user.id)
    
    # Schedule welcome email
    background_tasks.add_task(send_welcome, user.email, default_key_plaintext[:8])

    logger.info(
        "google_oauth_user_registered",
        user_id=str(user.id),
        email=user.email,
    )

    access_token = _create_access_token(str(user.id), user.role)
    refresh_token = _create_refresh_token(str(user.id))

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        default_key=default_key_plaintext,
    )


@router.post("/logout", status_code=status.HTTP_200_OK)
async def logout(body: LogoutRequest):
    """
    Logout — invalidate the refresh token by blacklisting its JTI in Redis.
    """
    try:
        payload = jwt.decode(
            body.refresh_token,
            settings.JWT_REFRESH_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
        )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )

    if payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
        )

    jti = payload.get("jti")
    exp = payload.get("exp")
    if not jti or not exp:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token payload",
        )

    # Blacklist the refresh token JTI until it expires
    redis = get_redis()
    ttl = max(int(exp - datetime.now(timezone.utc).timestamp()), 0)
    if ttl > 0:
        await redis.setex(
            f"{REDIS_REFRESH_BLACKLIST_PREFIX}{jti}", ttl, "1"
        )

    logger.info(
        "user_logged_out",
        user_id=payload.get("sub"),
        jti=jti,
    )

    return {"message": "Successfully logged out"}


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    """
    Return current authenticated user info.
    """
    return UserResponse(
        id=str(current_user.id),
        email=current_user.email,
        role=current_user.role,
        is_active=current_user.is_active,
        created_at=current_user.created_at.isoformat(),
    )

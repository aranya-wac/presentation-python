from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.config import settings
from app.core.database import get_db
from app.core.rate_limit import limiter
from app.models.user import User
from app.schemas.auth import (
    LoginRequest,
    MessageResponse,
    PasswordResetConfirm,
    PasswordResetRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserPublic,
)
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])

# Rate-limit window for password reset / register / login.
# Tight enough to make brute-force / enumeration impractical, loose enough
# to not break a user fat-fingering their password a couple of times.
_AUTH_LIMIT = "10/minute"
_RESET_LIMIT = "5/hour"


@router.post("/register", response_model=UserPublic, status_code=status.HTTP_201_CREATED)
@limiter.limit(_AUTH_LIMIT)
async def register(
    request: Request,
    req: RegisterRequest,
    db: AsyncSession = Depends(get_db),
) -> UserPublic:
    return await auth_service.register(db, req)


@router.post("/login", response_model=TokenResponse)
@limiter.limit(_AUTH_LIMIT)
async def login(
    request: Request,
    req: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    user = await auth_service.authenticate(db, req.email, req.password)
    return await auth_service.issue_tokens(db, user)


@router.post("/refresh", response_model=TokenResponse)
@limiter.limit("30/minute")
async def refresh(
    request: Request,
    req: RefreshRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    return await auth_service.rotate_refresh(db, req.refresh_token)


@router.get("/me", response_model=UserPublic)
async def me(current_user: User = Depends(get_current_user)) -> UserPublic:
    return UserPublic(
        id=str(current_user.id),
        email=current_user.email,
        full_name=current_user.full_name,
        role=current_user.role,
        is_active=current_user.is_active,
    )


@router.post("/forgot-password", response_model=MessageResponse)
@limiter.limit(_RESET_LIMIT)
async def forgot_password(
    request: Request,
    req: PasswordResetRequest,
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    token = await auth_service.request_password_reset(db, req.email)
    # Always 200 — never disclose whether the email exists.
    response = MessageResponse(
        message="If an account exists for that email, a reset link has been sent."
    )
    # Surface the token in dev mode so the flow is testable without an email service.
    if token and settings.SECRET_KEY == "local-dev-secret-change-me":
        response = MessageResponse(message=f"[dev] reset token: {token}")
    return response


@router.post("/reset-password", response_model=MessageResponse)
@limiter.limit(_RESET_LIMIT)
async def reset_password(
    request: Request,
    req: PasswordResetConfirm,
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    await auth_service.confirm_password_reset(db, req.token, req.new_password)
    return MessageResponse(message="Password updated. Please sign in with your new password.")

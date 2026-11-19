import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.config import settings
from app.core.security import get_current_user
from app.services.auth import (
    AuthError,
    AuthUser,
    authenticate,
    consume_password_reset_token,
    create_password_reset_token,
    create_user,
    find_user_by_email,
    find_user_by_id,
    issue_session,
    set_password,
    verify_password,
)
from app.services.email import (
    EmailNotConfiguredError,
    send_password_reset_email,
)


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/auth",
    tags=["Auth"],
)


class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str | None = None


class LoginRequest(BaseModel):
    email: str
    password: str


class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    token: str
    password: str


class ChangePasswordRequest(BaseModel):
    password: str
    current_password: str | None = None


def _raise(exc: AuthError):
    raise HTTPException(status_code=exc.status_code, detail=exc.message)


@router.post("/register")
async def register(payload: RegisterRequest):
    try:
        user = create_user(payload.email, payload.password, payload.name)
    except AuthError as exc:
        _raise(exc)

    return issue_session(user)


@router.post("/login")
async def login(payload: LoginRequest):
    try:
        user = authenticate(payload.email, payload.password)
    except AuthError as exc:
        _raise(exc)

    return issue_session(user)


@router.post("/forgot-password")
async def forgot_password(payload: ForgotPasswordRequest):
    """
    Always answers the same way so the endpoint cannot be used to
    discover which emails have accounts.
    """
    user = find_user_by_email(payload.email)

    if user:
        token = create_password_reset_token(user)
        reset_url = (
            f"{settings.frontend_url.rstrip('/')}/reset-password?token={token}"
        )

        try:
            await send_password_reset_email(
                user["email"],
                reset_url,
                settings.password_reset_expires_minutes,
            )
        except EmailNotConfiguredError:
            logger.warning(
                "Password reset requested but RESEND_API_KEY is not configured"
            )
        except Exception:
            logger.exception("Failed to send password reset email")

    return {
        "message": "If an account exists for this email, a reset link has been sent.",
    }


@router.post("/reset-password")
async def reset_password(payload: ResetPasswordRequest):
    try:
        user = consume_password_reset_token(payload.token)
        set_password(user["id"], payload.password)
    except AuthError as exc:
        _raise(exc)

    return {"message": "Password updated. Please sign in with your new password."}


@router.post("/change-password")
async def change_password(
    payload: ChangePasswordRequest,
    user: AuthUser = Depends(get_current_user),
):
    if getattr(user, "app_metadata", {}).get("provider") == "demo":
        raise HTTPException(
            status_code=400,
            detail="Demo accounts cannot change their password.",
        )

    try:
        if payload.current_password is not None:
            stored = find_user_by_id(user.id) or {}
            if not verify_password(payload.current_password, stored.get("password_hash")):
                raise AuthError("Current password is incorrect.", status_code=400)

        updated = set_password(user.id, payload.password)
    except AuthError as exc:
        _raise(exc)

    # Changing the password signs out other sessions; hand this one a
    # fresh token so the user stays signed in here.
    return issue_session(updated)

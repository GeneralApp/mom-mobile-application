from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.db import Client, get_admin_db, get_user_db
from app.core.demo_accounts import (
    is_demo_token,
    get_demo_user_from_token,
    ensure_demo_user_db_state,
)
from app.services.auth import AuthError, verify_access_token


security = HTTPBearer(
    auto_error=True,
)


def get_access_token(
    credentials: HTTPAuthorizationCredentials = Depends(
        security
    ),
) -> str:
    return credentials.credentials


def resolve_user(access_token: str):
    """
    Turn a bearer token into the calling user.

    Demo-account tokens map to their fixed demo user (whose profile and
    workspace are provisioned on first use). Everything else must be a
    JWT issued by /api/v1/auth.
    """
    if is_demo_token(access_token):
        demo_user = get_demo_user_from_token(access_token)

        if not demo_user:
            raise HTTPException(
                status_code=401,
                detail="Invalid demo token or demo account not found",
            )

        try:
            ensure_demo_user_db_state(get_admin_db(), demo_user)
        except Exception:
            pass

        return demo_user

    try:
        return verify_access_token(access_token)
    except AuthError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.message,
        )


def get_current_user(
    access_token: str = Depends(get_access_token),
):
    return resolve_user(access_token)


def get_db(
    access_token: str = Depends(get_access_token),
) -> Client:
    """
    Database client scoped to the caller: it can only read and change
    rows the caller is allowed to (see app/db/policies.py).
    """
    user = resolve_user(access_token)

    try:
        return get_user_db(user)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to initialize database client: {exc}",
        )


def get_authenticated_user(
    db: Client,
    access_token: str,
):
    """
    Return the user the request's database client was created for.
    """
    auth_user = getattr(db, "auth_user", None)

    if auth_user is not None:
        return auth_user

    return resolve_user(access_token)

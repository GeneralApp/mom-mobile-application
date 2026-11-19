"""
Email + password authentication backed by MongoDB.

Passwords are hashed with scrypt; sessions are HS256 JWTs signed with JWT_SECRET. Each user carries a `token_version`
that is embedded in their tokens, so changing or resetting a password
signs out every existing session.
"""

import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from app.core.config import settings
from app.db import get_admin_db
from app.db.query import utcnow


JWT_ALGORITHM = "HS256"
JWT_ISSUER = "mom-creator"
JWT_AUDIENCE = "authenticated"

_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1


class AuthError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class AuthUser:
    """The authenticated caller. Same shape the routes already use."""

    def __init__(self, id: str, email: str, user_metadata: dict | None = None, provider: str = "email"):
        self.id = id
        self.email = email
        self.user_metadata = user_metadata or {}
        self.app_metadata = {"provider": provider}
        self.role = "authenticated"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "email": self.email,
            "user_metadata": self.user_metadata,
            "app_metadata": self.app_metadata,
            "role": self.role,
        }


# ============================================================
# PASSWORDS
# ============================================================

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=64,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    try:
        scheme, n, r, p, salt_hex, digest_hex = str(stored or "").split("$")
    except ValueError:
        return False

    if scheme != "scrypt":
        return False

    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=bytes.fromhex(salt_hex),
        n=int(n),
        r=int(r),
        p=int(p),
        dklen=len(digest_hex) // 2,
    )
    return hmac.compare_digest(digest.hex(), digest_hex)


def validate_new_password(password: str) -> None:
    if len(password or "") < 8:
        raise AuthError("Password must be at least 8 characters long.")

    if len(password) > 256:
        raise AuthError("Password is too long.")


def normalize_email(email: str) -> str:
    return str(email or "").strip().lower()


# ============================================================
# USERS
# ============================================================

def find_user_by_email(email: str) -> dict | None:
    response = (
        get_admin_db()
        .table("users")
        .select("*")
        .eq("email", normalize_email(email))
        .maybe_single()
        .execute()
    )
    return response.data


def find_user_by_id(user_id: str) -> dict | None:
    response = (
        get_admin_db()
        .table("users")
        .select("*")
        .eq("id", str(user_id))
        .maybe_single()
        .execute()
    )
    return response.data


def _auth_user(row: dict) -> AuthUser:
    return AuthUser(
        id=row["id"],
        email=row["email"],
        user_metadata=row.get("user_metadata") or {},
        provider=row.get("provider") or "email",
    )


def create_user(
    email: str,
    password: str,
    name: str | None = None,
    user_id: str | None = None,
) -> dict:
    email = normalize_email(email)

    if not email or "@" not in email:
        raise AuthError("Please enter a valid email address.")

    validate_new_password(password)

    if find_user_by_email(email):
        raise AuthError("User already registered", status_code=409)

    display_name = (name or "").strip() or email.split("@")[0]
    admin = get_admin_db()

    row = (
        admin.table("users")
        .insert(
            {
                "id": user_id or str(uuid.uuid4()),
                "email": email,
                "password_hash": hash_password(password),
                "token_version": 0,
                "user_metadata": {"name": display_name, "full_name": display_name},
                "provider": "email",
            }
        )
        .execute()
        .data[0]
    )

    # handle_new_user(): every auth user gets a profile.
    existing_profile = (
        admin.table("profiles").select("id").eq("id", row["id"]).execute()
    )

    if not existing_profile.data:
        admin.table("profiles").insert(
            {"id": row["id"], "name": display_name[:150]}
        ).execute()

    return row


def set_password(user_id: str, password: str) -> dict:
    validate_new_password(password)
    user = find_user_by_id(user_id)

    if not user:
        raise AuthError("User not found", status_code=404)

    return (
        get_admin_db()
        .table("users")
        .update(
            {
                "password_hash": hash_password(password),
                "token_version": int(user.get("token_version") or 0) + 1,
            }
        )
        .eq("id", user_id)
        .execute()
        .data[0]
    )


def authenticate(email: str, password: str) -> dict:
    user = find_user_by_email(email)

    if not user or not verify_password(password or "", user.get("password_hash")):
        raise AuthError("Invalid login credentials", status_code=400)

    get_admin_db().table("users").update(
        {"last_sign_in_at": utcnow()}
    ).eq("id", user["id"]).execute()

    return user


# ============================================================
# SESSIONS (JWT)
# ============================================================

def issue_session(user: dict) -> dict:
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=settings.jwt_expires_minutes)

    token = jwt.encode(
        {
            "sub": user["id"],
            "email": user["email"],
            "ver": int(user.get("token_version") or 0),
            "iat": int(now.timestamp()),
            "exp": int(expires.timestamp()),
            "iss": JWT_ISSUER,
            "aud": JWT_AUDIENCE,
        },
        settings.jwt_secret,
        algorithm=JWT_ALGORITHM,
    )

    auth_user = _auth_user(user)

    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": int((expires - now).total_seconds()),
        "expires_at": int(expires.timestamp()),
        "user": auth_user.to_dict(),
    }


def verify_access_token(token: str) -> AuthUser:
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[JWT_ALGORITHM],
            audience=JWT_AUDIENCE,
            issuer=JWT_ISSUER,
        )
    except jwt.ExpiredSignatureError:
        raise AuthError("Session expired. Please sign in again.", status_code=401)
    except jwt.PyJWTError:
        raise AuthError("Invalid authentication token", status_code=401)

    user = find_user_by_id(claims.get("sub", ""))

    if not user or int(user.get("token_version") or 0) != int(claims.get("ver", -1)):
        raise AuthError("Invalid authentication token", status_code=401)

    return _auth_user(user)


# ============================================================
# PASSWORD RESET
# ============================================================

def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def create_password_reset_token(user: dict) -> str:
    raw = secrets.token_urlsafe(32)
    database = get_admin_db().database

    database["password_reset_tokens"].delete_many({"user_id": user["id"]})
    database["password_reset_tokens"].insert_one(
        {
            "token_hash": _hash_token(raw),
            "user_id": user["id"],
            "created_at": utcnow(),
            "expires_at": utcnow() + timedelta(minutes=settings.password_reset_expires_minutes),
        }
    )
    return raw


def consume_password_reset_token(raw: str) -> dict:
    database = get_admin_db().database

    record = database["password_reset_tokens"].find_one_and_delete(
        {"token_hash": _hash_token(str(raw or ""))}
    )

    if not record:
        raise AuthError("This password reset link is invalid or has expired.", status_code=400)

    expires_at: Any = record.get("expires_at")

    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if not expires_at or expires_at < utcnow():
        raise AuthError("This password reset link is invalid or has expired.", status_code=400)

    user = find_user_by_id(record["user_id"])

    if not user:
        raise AuthError("This password reset link is invalid or has expired.", status_code=400)

    return user

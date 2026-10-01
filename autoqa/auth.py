"""JWT verification and account/session checks shared by the Production HTTP surface."""
import base64
import hashlib
import hmac
from dataclasses import dataclass
from datetime import timedelta, timezone
from uuid import uuid4

import jwt
from fastapi import HTTPException, Request

from .product_models import SessionRow, UserRow
from .store_base import now

ACCESS_SECONDS = 15 * 60
REFRESH_SECONDS = 7 * 24 * 60 * 60
ISSUER = "autoqa-studio"
AUDIENCE = "autoqa-production"


@dataclass(frozen=True)
class CurrentUser:
    user_id: str
    email: str
    nickname: str
    workspace_id: str
    role: str
    session_hash: str
    access_expires_at: int


def current_user(request: Request) -> CurrentUser:
    identity = getattr(request.state, "current_user", None)
    if identity is None:
        raise HTTPException(401, "请先登录")
    return identity


def user_info(user):
    # Explicit allowlist: no ORM serialization of credential fields.
    return {"id": user.id, "email": user.email, "nickname": user.nickname,
            "is_active": user.is_active, "created_at": user.created_at, "updated_at": user.updated_at}


class Tokens:
    def __init__(self, master_key):
        # Separate signing purpose from the existing AES key without new required deployment settings.
        self.key = hmac.new(base64.urlsafe_b64decode(master_key), b"autoqa-jwt-v1", hashlib.sha256).digest()

    def encode(self, user_id, session_hash, kind, expires_at):
        instant = int(now().timestamp())
        return jwt.encode({"sub": user_id, "sid": session_hash, "type": kind, "jti": str(uuid4()),
            "iat": instant, "nbf": instant, "exp": int(expires_at.timestamp()),
            "iss": ISSUER, "aud": AUDIENCE}, self.key, algorithm="HS256")

    def pair(self, user_id, session_hash, expires_at):
        access_end = min(now() + timedelta(seconds=ACCESS_SECONDS), expires_at)
        return {"access_token": self.encode(user_id, session_hash, "access", access_end),
                "refresh_token": self.encode(user_id, session_hash, "refresh", expires_at),
                "token_type": "bearer", "expires_in": max(0, int((access_end - now()).total_seconds()))}

    def decode(self, raw, kind):
        try:
            if not raw or len(raw) > 4096:
                raise ValueError
            claims = jwt.decode(raw, self.key, algorithms=["HS256"], issuer=ISSUER, audience=AUDIENCE,
                options={"require": ["sub", "sid", "type", "jti", "iat", "nbf", "exp", "iss", "aud"]})
            if claims["type"] != kind or not isinstance(claims["sid"], str) or len(claims["sid"]) != 64:
                raise ValueError
            return claims
        except (jwt.InvalidTokenError, ValueError, TypeError):
            raise HTTPException(401, "登录已过期，请重新登录") from None

    def identity(self, session, claims):
        saved = session.get(SessionRow, claims["sid"])
        user = session.get(UserRow, claims["sub"])
        if (not saved or not saved.refresh_token_hash or saved.user_id != claims["sub"]
                or saved.expires_at.replace(tzinfo=timezone.utc) <= now() or not user or not user.is_active):
            raise HTTPException(401, "登录已过期，请重新登录")
        return user, saved

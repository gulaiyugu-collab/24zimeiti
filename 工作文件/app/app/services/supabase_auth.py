"""Supabase JWT authentication shared by the public API and cloud control plane."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import jwt
from fastapi import Header, HTTPException, status


class SupabaseAuthError(ValueError):
    """A request token is missing, invalid, expired, or misconfigured."""


@dataclass(frozen=True)
class AuthenticatedUser:
    user_id: str
    role: str | None = None
    email: str | None = None


def _env_text(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    return value or None


def _project_url() -> str | None:
    value = _env_text("SUPABASE_URL")
    return value.rstrip("/") if value else None


@lru_cache(maxsize=4)
def _jwks_client(url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(f"{url}/auth/v1/.well-known/jwks.json")


class SupabaseJWTAuthenticator:
    """Verify Supabase access tokens with a legacy secret or the project JWKS."""

    def __init__(
        self,
        *,
        project_url: str | None = None,
        jwt_secret: str | None = None,
        audience: str = "authenticated",
        issuer: str | None = None,
    ) -> None:
        self.project_url = (project_url or _project_url() or "").rstrip("/")
        self.jwt_secret = jwt_secret or _env_text("SUPABASE_JWT_SECRET")
        self.audience = audience
        self.issuer = issuer or (f"{self.project_url}/auth/v1" if self.project_url else None)

    def verify(self, token: str) -> AuthenticatedUser:
        candidate = token.strip()
        if not candidate:
            raise SupabaseAuthError("missing bearer token")

        try:
            header = jwt.get_unverified_header(candidate)
            algorithm = str(header.get("alg") or "")
            if algorithm == "HS256":
                if not self.jwt_secret:
                    raise SupabaseAuthError("HS256 verification requires SUPABASE_JWT_SECRET")
                key: Any = self.jwt_secret
            elif algorithm in {"RS256", "ES256"}:
                if not self.project_url:
                    raise SupabaseAuthError("JWKS verification requires SUPABASE_URL")
                key = _jwks_client(self.project_url).get_signing_key_from_jwt(candidate).key
            else:
                raise SupabaseAuthError("unsupported JWT algorithm")

            claims = jwt.decode(
                candidate,
                key,
                algorithms=[algorithm],
                audience=self.audience,
                issuer=self.issuer,
                options={"require": ["sub", "exp", "iat"]},
            )
        except SupabaseAuthError:
            raise
        except (jwt.PyJWTError, ValueError) as exc:
            raise SupabaseAuthError("invalid Supabase access token") from exc

        user_id = str(claims.get("sub") or "").strip()
        if not user_id:
            raise SupabaseAuthError("token subject is missing")
        return AuthenticatedUser(
            user_id=user_id,
            role=str(claims.get("role") or "") or None,
            email=str(claims.get("email") or "") or None,
        )


def require_supabase_user(
    authorization: str | None = Header(default=None),
) -> AuthenticatedUser:
    """FastAPI dependency for routes that require a real Supabase login."""
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.casefold() != "bearer" or not token.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="需要 Supabase 登录令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return SupabaseJWTAuthenticator().verify(token)
    except SupabaseAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Supabase 登录令牌无效或已过期",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


"""Self-contained authentication for a mainland-only control plane."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import uuid
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import jwt


class DomesticAuthError(ValueError):
    pass


def _database_path() -> Path:
    configured = os.getenv("PROJECT024_CLOUD_TASK_DB", "").strip()
    return Path(configured or "var/cloud-control.sqlite3").expanduser().resolve()


class LocalAuthStore:
    def __init__(self, database_path: str | Path | None = None) -> None:
        self.database_path = Path(database_path or _database_path()).resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS cloud_users ("
                "id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, "
                "created_at TEXT NOT NULL)"
            )
            connection.commit()

    @staticmethod
    def _hash_password(password: str, salt: bytes | None = None) -> str:
        if len(password) < 8:
            raise DomesticAuthError("密码至少需要 8 位")
        salt = salt or secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 210_000)
        return f"pbkdf2_sha256$210000${salt.hex()}${digest.hex()}"

    @staticmethod
    def _verify_password(password: str, encoded: str) -> bool:
        try:
            algorithm, rounds, salt_hex, digest_hex = encoded.split("$", 3)
            if algorithm != "pbkdf2_sha256":
                return False
            candidate = hashlib.pbkdf2_hmac(
                "sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds)
            )
            return hmac.compare_digest(candidate.hex(), digest_hex)
        except (TypeError, ValueError):
            return False

    def register(self, email: str, password: str) -> dict[str, str]:
        normalized = email.strip().lower()
        if "@" not in normalized or len(normalized) > 240:
            raise DomesticAuthError("请输入有效邮箱")
        user_id = str(uuid.uuid4())
        record = (user_id, normalized, self._hash_password(password), datetime.now(UTC).isoformat())
        try:
            with closing(sqlite3.connect(self.database_path)) as connection:
                connection.execute(
                    "INSERT INTO cloud_users(id,email,password_hash,created_at) VALUES (?,?,?,?)",
                    record,
                )
                connection.commit()
        except sqlite3.IntegrityError as exc:
            raise DomesticAuthError("该邮箱已经注册") from exc
        return {"user_id": user_id, "email": normalized}

    def authenticate(self, email: str, password: str) -> dict[str, str]:
        normalized = email.strip().lower()
        with closing(sqlite3.connect(self.database_path)) as connection:
            row = connection.execute(
                "SELECT id,email,password_hash FROM cloud_users WHERE email = ?", (normalized,)
            ).fetchone()
        if not row or not self._verify_password(password, str(row[2])):
            raise DomesticAuthError("邮箱或密码错误")
        return {"user_id": str(row[0]), "email": str(row[1])}


class LocalJWTAuthenticator:
    def __init__(self, *, secret: str | None = None, issuer: str = "project024-domestic", audience: str = "project024") -> None:
        self.secret = (secret or os.getenv("PROJECT024_AUTH_SECRET", "")).strip()
        self.issuer = issuer
        self.audience = audience

    def _require_secret(self) -> str:
        if len(self.secret) < 32:
            raise DomesticAuthError("PROJECT024_AUTH_SECRET 至少需要 32 个字符")
        return self.secret

    def issue(self, user: dict[str, str], expires_hours: int = 168) -> str:
        now = datetime.now(UTC)
        return jwt.encode(
            {"sub": user["user_id"], "email": user["email"], "role": "user", "iat": now, "exp": now + timedelta(hours=expires_hours), "iss": self.issuer, "aud": self.audience},
            self._require_secret(),
            algorithm="HS256",
        )

    def verify(self, token: str) -> Any:
        from .supabase_auth import AuthenticatedUser

        try:
            claims = jwt.decode(token.strip(), self._require_secret(), algorithms=["HS256"], issuer=self.issuer, audience=self.audience, options={"require": ["sub", "exp", "iat"]})
        except (jwt.PyJWTError, DomesticAuthError) as exc:
            raise DomesticAuthError("本地登录令牌无效或已过期") from exc
        return AuthenticatedUser(user_id=str(claims["sub"]), role=str(claims.get("role") or "user"), email=str(claims.get("email") or "") or None)

from __future__ import annotations

import os
import time
import unittest
from unittest.mock import patch

import jwt
from fastapi.testclient import TestClient

from app.services.supabase_auth import SupabaseJWTAuthenticator, SupabaseAuthError


class SupabaseAuthTests(unittest.TestCase):
    def _token(self, **overrides: object) -> str:
        now = int(time.time())
        claims = {
            "sub": "11111111-1111-1111-1111-111111111111",
            "role": "authenticated",
            "aud": "authenticated",
            "iss": "https://example.supabase.co/auth/v1",
            "iat": now,
            "exp": now + 300,
        }
        claims.update(overrides)
        return jwt.encode(claims, "test-secret-0123456789-abcdefghijkl", algorithm="HS256")

    def test_valid_hs256_token_returns_user(self) -> None:
        auth = SupabaseJWTAuthenticator(
            project_url="https://example.supabase.co",
            jwt_secret="test-secret-0123456789-abcdefghijkl",
        )
        user = auth.verify(self._token(email="a@example.com"))
        self.assertEqual("11111111-1111-1111-1111-111111111111", user.user_id)
        self.assertEqual("a@example.com", user.email)

    def test_wrong_secret_is_rejected(self) -> None:
        auth = SupabaseJWTAuthenticator(
            project_url="https://example.supabase.co",
            jwt_secret="wrong-secret",
        )
        with self.assertRaises(SupabaseAuthError):
            auth.verify(self._token())

    def test_wrong_audience_is_rejected(self) -> None:
        auth = SupabaseJWTAuthenticator(
            project_url="https://example.supabase.co",
            jwt_secret="test-secret-0123456789-abcdefghijkl",
        )
        with self.assertRaises(SupabaseAuthError):
            auth.verify(self._token(aud="anon"))

    def test_expired_token_is_rejected(self) -> None:
        auth = SupabaseJWTAuthenticator(
            project_url="https://example.supabase.co",
            jwt_secret="test-secret-0123456789-abcdefghijkl",
        )
        with self.assertRaises(SupabaseAuthError):
            auth.verify(self._token(exp=int(time.time()) - 10))

    def test_dependency_requires_bearer_header(self) -> None:
        from app.services.supabase_auth import require_supabase_user
        from fastapi import FastAPI

        app = FastAPI()

        @app.get("/private")
        def private(user=__import__("fastapi").Depends(require_supabase_user)):
            return {"user_id": user.user_id}

        with patch.dict(os.environ, {"SUPABASE_JWT_SECRET": "test-secret-0123456789-abcdefghijkl", "SUPABASE_URL": "https://example.supabase.co"}):
            client = TestClient(app)
            response = client.get("/private")
        self.assertEqual(401, response.status_code)


if __name__ == "__main__":
    unittest.main()

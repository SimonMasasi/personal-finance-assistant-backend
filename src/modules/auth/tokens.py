"""Access-token encoding/decoding, plus refresh-token minting.

Access tokens are JWTs: self-contained, so `/v1/sync` validates one without a
database round trip. Refresh tokens are *not* JWTs — they are opaque random
strings stored hashed, because a refresh token must be revocable and a
self-contained token cannot be revoked.
"""

import hashlib
import secrets
import uuid as uuid_lib
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from src.core.config import SETTINGS
from src.shared.errors import AuthError


class JWTAuth:
    def encode_access_token(self, user_id: uuid_lib.UUID) -> str:
        now = datetime.now(tz=UTC)
        expires_at = now + timedelta(seconds=SETTINGS.JWT_ACCESS_TOKEN_EXPIRE_SECONDS)
        payload = {
            "sub": str(user_id),
            "iat": int(now.timestamp()),
            "exp": int(expires_at.timestamp()),
            "iss": SETTINGS.JWT_ISSUER,
            "type": "access",
        }
        return jwt.encode(payload, SETTINGS.SECRET_KEY, algorithm=SETTINGS.JWT_ALGORITHM)

    def decode(self, token: str) -> dict[str, Any]:
        try:
            payload: dict[str, Any] = jwt.decode(
                token,
                SETTINGS.SECRET_KEY,
                algorithms=[SETTINGS.JWT_ALGORITHM],
                issuer=SETTINGS.JWT_ISSUER,
                options={"require": ["exp", "iat", "sub", "iss"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthError("Access token has expired.") from exc
        except jwt.InvalidTokenError as exc:
            # Deliberately not echoing the library's message — it distinguishes
            # "bad signature" from "wrong issuer", which only helps an attacker.
            raise AuthError("Invalid access token.") from exc
        return payload

    @staticmethod
    def new_refresh_token() -> str:
        """A fresh opaque refresh token. 32 bytes of urandom, url-safe."""
        return secrets.token_urlsafe(32)

    @staticmethod
    def hash_refresh_token(token: str) -> str:
        """SHA-256, not Argon2.

        Argon2's cost exists to slow brute force against low-entropy secrets.
        This token already has 256 bits of entropy, so brute force is off the
        table and a slow hash would only tax every refresh call.
        """
        return hashlib.sha256(token.encode()).hexdigest()

    @staticmethod
    def refresh_expiry() -> datetime:
        return datetime.now(tz=UTC) + timedelta(seconds=SETTINGS.REFRESH_TOKEN_EXPIRE_SECONDS)

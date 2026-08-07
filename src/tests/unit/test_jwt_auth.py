import uuid as uuid_lib
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import jwt
import pytest

from config import SETTINGS
from src.shared.errors import AuthError
from src.utils.jwt_auth import JWTAuth


@pytest.fixture
def auth() -> JWTAuth:
    return JWTAuth()


class TestAccessTokens:
    def test_round_trip(self, auth: JWTAuth) -> None:
        user_id = uuid_lib.uuid4()
        payload = auth.decode(auth.encode_access_token(user_id))

        assert payload["sub"] == str(user_id)
        assert payload["type"] == "access"
        assert payload["iss"] == SETTINGS.JWT_ISSUER

    def test_expired_token_is_rejected(self, auth: JWTAuth) -> None:
        past = datetime.now(tz=UTC) - timedelta(hours=2)
        with patch("src.utils.jwt_auth.datetime") as mock_datetime:
            mock_datetime.now.return_value = past
            token = auth.encode_access_token(uuid_lib.uuid4())

        with pytest.raises(AuthError, match="expired"):
            auth.decode(token)

    def test_tampered_token_is_rejected(self, auth: JWTAuth) -> None:
        token = auth.encode_access_token(uuid_lib.uuid4())
        with pytest.raises(AuthError):
            auth.decode(token[:-4] + "aaaa")

    def test_token_from_another_issuer_is_rejected(self, auth: JWTAuth) -> None:
        token = jwt.encode(
            {
                "sub": str(uuid_lib.uuid4()),
                "iat": int(datetime.now(tz=UTC).timestamp()),
                "exp": int((datetime.now(tz=UTC) + timedelta(hours=1)).timestamp()),
                "iss": "some-other-service",
                "type": "access",
            },
            SETTINGS.SECRET_KEY,
            algorithm="HS256",
        )
        with pytest.raises(AuthError):
            auth.decode(token)

    def test_alg_none_is_rejected(self, auth: JWTAuth) -> None:
        """The classic JWT bypass. The client's mock adapter even emits
        {"alg": "none"} tokens, so this is not hypothetical here."""
        token = jwt.encode(
            {
                "sub": str(uuid_lib.uuid4()),
                "iat": int(datetime.now(tz=UTC).timestamp()),
                "exp": int((datetime.now(tz=UTC) + timedelta(hours=1)).timestamp()),
                "iss": SETTINGS.JWT_ISSUER,
                "type": "access",
            },
            key="",
            algorithm="none",
        )
        with pytest.raises(AuthError):
            auth.decode(token)

    def test_error_message_does_not_leak_the_reason(self, auth: JWTAuth) -> None:
        """"Invalid" for every failure mode except expiry — distinguishing a bad
        signature from a wrong issuer only helps an attacker."""
        with pytest.raises(AuthError) as exc:
            auth.decode("garbage.garbage.garbage")
        assert str(exc.value) == "Invalid access token."


class TestRefreshTokens:
    def test_tokens_are_unique(self) -> None:
        tokens = {JWTAuth.new_refresh_token() for _ in range(100)}
        assert len(tokens) == 100

    def test_tokens_have_enough_entropy(self) -> None:
        # token_urlsafe(32) yields 43 base64url characters.
        assert len(JWTAuth.new_refresh_token()) >= 43

    def test_hash_is_stable_and_hex(self) -> None:
        token = JWTAuth.new_refresh_token()
        digest = JWTAuth.hash_refresh_token(token)

        assert digest == JWTAuth.hash_refresh_token(token)
        assert len(digest) == 64
        assert int(digest, 16) >= 0

    def test_hash_does_not_contain_the_token(self) -> None:
        token = JWTAuth.new_refresh_token()
        assert token not in JWTAuth.hash_refresh_token(token)

    def test_expiry_is_in_the_future(self) -> None:
        assert JWTAuth.refresh_expiry() > datetime.now(tz=UTC)

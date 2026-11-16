"""The Google ID token verifier, tested against locally minted RS256 tokens.

No network: an RSA key is generated here and `PyJWKClient` is patched to return
it, so these run in CI and assert the checks themselves rather than Google's
availability. Every case below is a way a forged or unusable token could
otherwise become a session.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientConnectionError, PyJWKClientError

from src.modules.auth import google as google_auth
from src.modules.auth.google import GoogleIdTokenVerifier
from src.shared.errors import AuthError, ServiceUnavailableError

AUDIENCE = "test-web-client.apps.googleusercontent.com"

_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_public_key = _private_key.public_key()


def make_token(algorithm: str = "RS256", **overrides: Any) -> str:
    """Mint a token. Pass `_OMIT` for a claim to leave it out entirely."""
    now = datetime.now(tz=UTC)
    claims: dict[str, Any] = {
        "iss": "https://accounts.google.com",
        "aud": AUDIENCE,
        "sub": "1234567890",
        "email": "person@example.com",
        "email_verified": True,
        "name": "A Person",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=1)).timestamp()),
    }
    claims.update(overrides)
    for omitted in [k for k, v in claims.items() if v is _OMIT]:
        del claims[omitted]

    signing_key: Any = "" if algorithm == "none" else _private_key
    return jwt.encode(claims, signing_key, algorithm=algorithm)


class _Omit:
    """Sentinel so a test can remove a claim rather than blank it."""


_OMIT = _Omit()


@pytest.fixture(autouse=True)
def _local_signing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Key:
        key = _public_key

    monkeypatch.setattr(
        google_auth._jwks_client, "get_signing_key_from_jwt", lambda _token: _Key()
    )


@pytest.fixture(autouse=True)
def _audience(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(google_auth.SETTINGS, "GOOGLE_CLIENT_IDS", AUDIENCE)


@pytest.fixture
def verifier() -> GoogleIdTokenVerifier:
    return GoogleIdTokenVerifier()


class TestAcceptsValidTokens:
    async def test_returns_the_identity(self, verifier: GoogleIdTokenVerifier) -> None:
        identity = await verifier.verify(make_token())

        assert identity.sub == "1234567890"
        assert identity.email == "person@example.com"
        assert identity.display_name == "A Person"

    @pytest.mark.parametrize(
        "issuer", ["accounts.google.com", "https://accounts.google.com"]
    )
    async def test_accepts_both_issuer_spellings(
        self, verifier: GoogleIdTokenVerifier, issuer: str
    ) -> None:
        # Google emits either. Rejecting one fails real sign-ins intermittently.
        assert await verifier.verify(make_token(iss=issuer))

    async def test_accepts_email_verified_as_the_string_true(
        self, verifier: GoogleIdTokenVerifier
    ) -> None:
        assert await verifier.verify(make_token(email_verified="true"))

    async def test_accepts_one_of_several_configured_audiences(
        self, verifier: GoogleIdTokenVerifier, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            google_auth.SETTINGS, "GOOGLE_CLIENT_IDS", f"other-client, {AUDIENCE}"
        )
        assert await verifier.verify(make_token())

    async def test_falls_back_to_given_name_then_email_local_part(
        self, verifier: GoogleIdTokenVerifier
    ) -> None:
        # display_name is NOT NULL in the database and `name` is not guaranteed.
        by_given = await verifier.verify(make_token(name=_OMIT, given_name="Ada"))
        assert by_given.display_name == "Ada"

        by_email = await verifier.verify(make_token(name=_OMIT))
        assert by_email.display_name == "person"


class TestRejectsForgedOrUnusableTokens:
    async def test_rejects_a_token_for_another_app(
        self, verifier: GoogleIdTokenVerifier
    ) -> None:
        # The whole point of checking `aud`: a valid Google token minted for a
        # different application must not open a session here.
        with pytest.raises(AuthError):
            await verifier.verify(make_token(aud="someone-elses-client-id"))

    async def test_rejects_an_unexpected_issuer(
        self, verifier: GoogleIdTokenVerifier
    ) -> None:
        with pytest.raises(AuthError):
            await verifier.verify(make_token(iss="https://evil.example"))

    async def test_rejects_an_expired_token(
        self, verifier: GoogleIdTokenVerifier
    ) -> None:
        past = datetime.now(tz=UTC) - timedelta(hours=2)
        with pytest.raises(AuthError):
            await verifier.verify(make_token(exp=int(past.timestamp())))

    async def test_rejects_a_token_signed_by_someone_else(
        self, verifier: GoogleIdTokenVerifier
    ) -> None:
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        forged = jwt.encode(
            {
                "iss": "https://accounts.google.com",
                "aud": AUDIENCE,
                "sub": "1234567890",
                "email": "person@example.com",
                "email_verified": True,
                "iat": int(datetime.now(tz=UTC).timestamp()),
                "exp": int((datetime.now(tz=UTC) + timedelta(hours=1)).timestamp()),
            },
            other,
            algorithm="RS256",
        )
        with pytest.raises(AuthError):
            await verifier.verify(forged)

    async def test_rejects_an_unsigned_token(
        self, verifier: GoogleIdTokenVerifier
    ) -> None:
        # `alg: none` is the classic bypass, and the reason algorithms=["RS256"]
        # is passed explicitly rather than read off the token header.
        with pytest.raises(AuthError):
            await verifier.verify(make_token(algorithm="none"))

    @pytest.mark.parametrize("value", [False, "false", None, _OMIT])
    async def test_rejects_an_unverified_email(
        self, verifier: GoogleIdTokenVerifier, value: Any
    ) -> None:
        # "false" is the case a truthiness check would wave through — and an
        # unverified address means anyone able to register it owns the account.
        with pytest.raises(AuthError):
            await verifier.verify(make_token(email_verified=value))

    async def test_rejects_a_token_with_no_email(
        self, verifier: GoogleIdTokenVerifier
    ) -> None:
        with pytest.raises(AuthError):
            await verifier.verify(make_token(email=_OMIT))

    @pytest.mark.parametrize("claim", ["sub", "exp", "iat", "aud", "iss"])
    async def test_rejects_a_token_missing_a_required_claim(
        self, verifier: GoogleIdTokenVerifier, claim: str
    ) -> None:
        overrides: dict[str, Any] = {claim: _OMIT}
        with pytest.raises(AuthError):
            await verifier.verify(make_token(**overrides))


class TestGoogleBeingUnreachable:
    async def test_is_a_503_not_a_401(
        self, verifier: GoogleIdTokenVerifier, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The distinction the client acts on.

        401 makes it sign the user out; 5xx makes it back off and retry. A
        Google outage reported as 401 would sign out every user for its
        duration.
        """

        def _boom(_token: str) -> None:
            raise PyJWKClientConnectionError("unreachable")

        monkeypatch.setattr(
            google_auth._jwks_client, "get_signing_key_from_jwt", _boom
        )

        with pytest.raises(ServiceUnavailableError) as caught:
            await verifier.verify(make_token())
        assert caught.value.status_code == 503

    async def test_a_malformed_token_is_a_401_not_a_500(
        self, verifier: GoogleIdTokenVerifier, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression: this reached production code as an unhandled DecodeError.

        The other tests here patch `get_signing_key_from_jwt`, which is exactly
        the call that raises — so they could never have caught it. Use the real
        client: it parses the token header before touching the network, so a
        garbage string fails offline and this test stays hermetic.
        """
        real_client = PyJWKClient("https://example.invalid/certs")
        monkeypatch.setattr(
            google_auth._jwks_client,
            "get_signing_key_from_jwt",
            real_client.get_signing_key_from_jwt,
        )

        with pytest.raises(AuthError):
            await verifier.verify("garbage")

    async def test_an_unknown_key_id_is_still_a_401(
        self, verifier: GoogleIdTokenVerifier, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # PyJWKClient re-fetches the key set before raising this, so a rotated
        # key would already have resolved. Reaching here means a bad token.
        def _missing(_token: str) -> None:
            raise PyJWKClientError("kid not found")

        monkeypatch.setattr(
            google_auth._jwks_client, "get_signing_key_from_jwt", _missing
        )

        with pytest.raises(AuthError):
            await verifier.verify(make_token())

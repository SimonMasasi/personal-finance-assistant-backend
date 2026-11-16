"""Verification of Google ID tokens.

The client signs in with Google on the device and sends us the resulting ID
token. This module is the only thing standing between that string and a session
on somebody's account, so it is deliberately explicit: every claim that matters
is checked here, in the open, rather than inside a library's defaults.

Why PyJWT rather than `google-auth`: PyJWT is already this project's JWT library
(`src/modules/auth/tokens.py`), and `google-auth` carries no HTTP transport of its
own — it would pull in `requests` plus four transitive packages to do what
`PyJWKClient` already does. One JWT library, one mental model.
"""

import logging
from dataclasses import dataclass

import anyio.to_thread
import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientConnectionError, PyJWKClientError

from src.core.config import SETTINGS
from src.shared.errors import AuthError, ServiceUnavailableError

logger = logging.getLogger(__name__)

_JWKS_URI = "https://www.googleapis.com/oauth2/v3/certs"

# Google emits either spelling and documents both as valid. Accepting only one
# rejects real tokens intermittently, which is the worst kind of bug to chase.
_ALLOWED_ISSUERS = ("accounts.google.com", "https://accounts.google.com")

# Constructing the client performs no I/O — keys are fetched lazily on first
# use — so module scope is right and keeps one cache for the whole process.
_jwks_client = PyJWKClient(
    _JWKS_URI,
    cache_jwk_set=True,
    lifespan=SETTINGS.GOOGLE_JWKS_CACHE_SECONDS,
    cache_keys=True,
    max_cached_keys=16,
    timeout=SETTINGS.GOOGLE_JWKS_TIMEOUT_SECONDS,
)


@dataclass(frozen=True)
class GoogleIdentity:
    """What we are willing to believe about a verified token."""

    sub: str
    email: str
    display_name: str


def _audiences() -> list[str]:
    """Split at use, like CORS_ALLOW_ORIGINS — a staging or iOS client is then
    a config change rather than a code change."""
    return [c.strip() for c in SETTINGS.GOOGLE_CLIENT_IDS.split(",") if c.strip()]


class GoogleIdTokenVerifier:
    """A class, not a function, so tests have exactly one attribute to swap.

    `AuthService` holds an instance the same way it holds `JWTAuth()`. That is
    the entire test seam — there is no debug flag and no bypass in this file.
    """

    async def verify(self, id_token: str) -> GoogleIdentity:
        key = await self._signing_key(id_token)
        claims = self._decode(id_token, key)
        return self._identity(claims)

    async def _signing_key(self, id_token: str) -> object:
        # PyJWKClient fetches over urllib and blocks. Doing that on the event
        # loop would stall every other request during a slow Google response.
        try:
            jwk = await anyio.to_thread.run_sync(
                _jwks_client.get_signing_key_from_jwt, id_token
            )
        except PyJWKClientConnectionError as exc:
            # We could not reach Google. That is our problem, not the user's —
            # see ServiceUnavailableError for why this must not be a 401.
            logger.warning("Could not fetch Google signing keys: %s", type(exc).__name__)
            raise ServiceUnavailableError(
                "Could not reach Google to verify your sign-in. Try again shortly."
            ) from exc
        except PyJWKClientError as exc:
            # A key id we cannot find. PyJWKClient already re-fetched the key
            # set once before giving up, so a legitimately rotated key would
            # have resolved — reaching here means the token is bad.
            raise AuthError("Could not verify your Google sign-in.") from exc
        except jwt.InvalidTokenError as exc:
            # `get_signing_key_from_jwt` parses the header before it looks at
            # any key, so a malformed token raises DecodeError right here —
            # before the network, and before `_decode` ever runs. Missing this
            # turned "id_token: garbage" into a 500 instead of a 401.
            raise AuthError("Could not verify your Google sign-in.") from exc
        return jwk.key

    def _decode(self, id_token: str, key: object) -> dict[str, object]:
        try:
            claims: dict[str, object] = jwt.decode(
                id_token,
                key,  # type: ignore[arg-type]
                # Never read the algorithm off the token. This is the same
                # discipline test_jwt_auth.py already pins for our own tokens.
                algorithms=["RS256"],
                audience=_audiences(),
                issuer=list(_ALLOWED_ISSUERS),
                leeway=SETTINGS.GOOGLE_ID_TOKEN_LEEWAY_SECONDS,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthError("Your Google sign-in has expired. Sign in again.") from exc
        except jwt.InvalidTokenError as exc:
            # Base class of InvalidAudienceError, InvalidIssuerError, bad
            # signature and missing-claim. One message for all of them: telling
            # a caller *which* check failed only helps someone probing.
            raise AuthError("Could not verify your Google sign-in.") from exc
        return claims

    def _identity(self, claims: dict[str, object]) -> GoogleIdentity:
        # PyJWT validated the signature, audience, issuer and expiry. These are
        # the claims it has no opinion about and we do.
        #
        # `email_verified` arrives as a bool from some Google endpoints and the
        # STRING "true" from others. A truthiness test would happily accept the
        # string "false", which is how an unverified address becomes an
        # identity — and with it, anyone who can register that address.
        if claims.get("email_verified") not in (True, "true"):
            raise AuthError("Your Google account's email address is not verified.")

        sub = claims.get("sub")
        if not isinstance(sub, str) or not sub:
            raise AuthError("Could not verify your Google sign-in.")

        email = claims.get("email")
        if not isinstance(email, str) or not email:
            # Only present when the `email` scope was granted. The client asks
            # for it by default; if it is missing we cannot identify anyone.
            raise AuthError("Your Google account did not share an email address.")

        return GoogleIdentity(sub=sub, email=email, display_name=_name(claims, email))


def _name(claims: dict[str, object], email: str) -> str:
    """`name` is not guaranteed, and display_name is NOT NULL in the database."""
    for field in ("name", "given_name"):
        value = claims.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return email.split("@")[0]

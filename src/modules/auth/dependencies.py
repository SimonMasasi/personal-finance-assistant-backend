import uuid as uuid_lib
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.modules.auth.tokens import JWTAuth
from src.shared.errors import AuthError

# auto_error=False so a missing header raises our AuthError (401 with the
# documented body shape) rather than FastAPI's bare 403.
bearer_scheme = HTTPBearer(auto_error=False)
jwt_auth = JWTAuth()


async def get_current_user_id(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> uuid_lib.UUID:
    """Resolve the caller to a user id.

    Returns the id, not a hydrated `User` row. Every authenticated endpoint here
    needs `user_id` and nothing else, so loading the user would be a database
    round trip per request that buys nothing. The tradeoff is that a deleted
    user's unexpired access token stays valid for up to 15 minutes; the
    `ON DELETE CASCADE` on their rows means it can read nothing.
    """
    if credentials is None:
        raise AuthError("Missing bearer token.")

    payload = jwt_auth.decode(credentials.credentials)

    # A refresh token is signed by the same key. Without this check it would be
    # accepted as an access token, defeating the short access lifetime.
    if payload.get("type") != "access":
        raise AuthError("Invalid token type.")

    try:
        return uuid_lib.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise AuthError("Invalid token subject.") from exc


CurrentUserId = Annotated[uuid_lib.UUID, Depends(get_current_user_id)]

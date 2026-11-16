"""Mint a real session for a local test user, without going through Google.

The Android client's live integration test used to create its account by
POSTing an email and password. That endpoint is gone, and the replacement needs
a real Google ID token — which a test cannot obtain unattended.

The alternative would have been a debug-only bypass on `/v1/auth/google`. That
is an authentication bypass living in production code, guarded by a setting
which currently defaults to True. This script exists instead: it needs database
credentials, which is a boundary that already means "you are an operator", and
it adds nothing at all to the running service.

It deliberately calls the SAME two functions the endpoint calls, rather than
reimplementing token issuance — so a token it prints is indistinguishable from
one a real sign-in produces, and it cannot drift from the real thing.

    uv run python -m src.modules.auth.mint_test_session --email live@example.com
    eval "$(uv run python -m src.modules.auth.mint_test_session --email live@example.com)"
"""

import argparse
import asyncio
import json

from sqlalchemy import select

from src.core.config import SETTINGS
from src.modules.auth.models import User
from src.modules.auth.services import AuthService
from src.shared.database import SessionFactory

# Google's `sub` is a numeric string, so this can never collide with a real one.
LOCAL_SUB_PREFIX = "local-test-"

_LOCAL_HOSTS = ("localhost", "127.0.0.1", "@db:", "@db/")


def _refuse_if_not_local() -> None:
    """A guard, not a security boundary — the credentials are that.

    It exists to stop the obvious accident: running this against a production
    database because a shell had the wrong DATABASE_URL exported.
    """
    url = SETTINGS.DATABASE_URL
    if SETTINGS.DEBUG or any(host in url for host in _LOCAL_HOSTS):
        return
    raise SystemExit(
        "Refusing to run: DEBUG is false and DATABASE_URL does not look local.\n"
        "This mints a valid session for an arbitrary email — it is a development tool."
    )


async def mint(email: str, display_name: str) -> dict[str, str]:
    service = AuthService()
    google_sub = f"{LOCAL_SUB_PREFIX}{email}"

    async with SessionFactory() as session:
        user = (
            await session.execute(select(User).where(User.google_sub == google_sub))
        ).scalar_one_or_none()

        # Idempotent: the integration test signs in several times and expects
        # the same account each time, exactly as a returning user would get.
        if user is None:
            user = User(email=email, google_sub=google_sub, display_name=display_name)
            session.add(user)
            await session.flush()

        raw_refresh, _ = await service._issue_refresh(session, user.id)
        access = service.jwt.encode_access_token(user.id)
        await session.commit()

        return {
            "access": access,
            "refresh": raw_refresh,
            "user_id": str(user.id),
            "email": user.email,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--display-name", default="Integration")
    parser.add_argument(
        "--json", action="store_true", help="emit JSON instead of shell exports"
    )
    args = parser.parse_args()

    _refuse_if_not_local()
    session = asyncio.run(mint(args.email, args.display_name))

    if args.json:
        print(json.dumps(session, indent=2))
        return

    for key, value in session.items():
        print(f"export PFA_LIVE_{key.upper()}='{value}'")


if __name__ == "__main__":
    main()

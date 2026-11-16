import uuid as uuid_lib
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.auth.dtos import (
    AuthResponse,
    DeviceRequest,
    GoogleSignInRequest,
    RefreshRequest,
    TokenPairResponse,
    UserDTO,
)
from src.modules.auth.google import GoogleIdentity, GoogleIdTokenVerifier
from src.modules.auth.models import Device, RefreshToken, User
from src.modules.auth.tokens import JWTAuth
from src.shared.errors import AuthError, ConflictError


class AuthService:
    def __init__(self) -> None:
        self.jwt = JWTAuth()
        # Held as an attribute for the same reason as `jwt`: it makes the
        # verifier one swappable object, which is the whole test seam. There is
        # no debug flag and no bypass anywhere in this module.
        self.google = GoogleIdTokenVerifier()

    # ── helpers ───────────────────────────────────────────────────────────

    def _user_dto(self, user: User) -> UserDTO:
        return UserDTO(id=str(user.id), email=user.email, display_name=user.display_name)

    async def _issue_refresh(
        self, session: AsyncSession, user_id: uuid_lib.UUID
    ) -> tuple[str, RefreshToken]:
        raw = self.jwt.new_refresh_token()
        row = RefreshToken(
            user_id=user_id,
            token_hash=self.jwt.hash_refresh_token(raw),
            expires_at=self.jwt.refresh_expiry(),
        )
        session.add(row)
        await session.flush()
        return raw, row

    # ── sign in with Google ───────────────────────────────────────────────

    async def google_sign_in(
        self, session: AsyncSession, payload: GoogleSignInRequest
    ) -> AuthResponse:
        """Exchange a verified Google ID token for our own session.

        One endpoint for both "first time here" and "welcome back", returning
        200 either way. A 201-on-create would tell any caller holding a valid
        token whether that account already existed, which is an account
        enumeration oracle for the price of a status code.
        """
        identity = await self.google.verify(payload.id_token)

        user = (
            await session.execute(select(User).where(User.google_sub == identity.sub))
        ).scalar_one_or_none()

        if user is None:
            user = await self._create_google_user(session, identity)
        else:
            # Google is authoritative for both of these and either can change.
            user.email = identity.email
            user.display_name = identity.display_name
            await session.flush()

        raw_refresh, _ = await self._issue_refresh(session, user.id)
        access = self.jwt.encode_access_token(user.id)
        await session.commit()
        return AuthResponse(access=access, refresh=raw_refresh, user=self._user_dto(user))

    async def _create_google_user(
        self, session: AsyncSession, identity: GoogleIdentity
    ) -> User:
        user = User(
            email=identity.email,
            google_sub=identity.sub,
            display_name=identity.display_name,
        )
        session.add(user)
        try:
            await session.flush()
        except IntegrityError as exc:
            # Let the unique indexes decide rather than a prior SELECT — a
            # check-then-act loses to a concurrent sign-in.
            #
            # Reaching here means the email is taken by a DIFFERENT google_sub
            # (the sub was just looked up and missed). Two Google accounts
            # cannot share a verified address, so in practice this is a Workspace
            # address that moved between accounts. Refusing is right: silently
            # reassigning the row would hand one person another's transactions.
            await session.rollback()
            raise ConflictError(
                "That email address is already linked to a different Google account."
            ) from exc
        return user

    # ── refresh (rotating, with reuse detection) ──────────────────────────

    async def refresh(self, session: AsyncSession, payload: RefreshRequest) -> TokenPairResponse:
        token_hash = self.jwt.hash_refresh_token(payload.refresh)
        row = (
            await session.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
        ).scalar_one_or_none()

        if row is None:
            raise AuthError("Invalid refresh token.")

        if row.revoked_at is not None:
            # This token was already exchanged. Either it leaked and the thief
            # is using it, or it leaked and the legitimate holder is — we cannot
            # tell which, so the whole chain dies and both parties re-authenticate.
            await self._revoke_chain(session, row)
            await session.commit()
            raise AuthError("Refresh token has been revoked.")

        if row.expires_at <= datetime.now(tz=UTC):
            raise AuthError("Refresh token has expired.")

        raw_refresh, new_row = await self._issue_refresh(session, row.user_id)
        row.revoked_at = datetime.now(tz=UTC)
        row.replaced_by_id = new_row.id

        access = self.jwt.encode_access_token(row.user_id)
        await session.commit()
        return TokenPairResponse(access=access, refresh=raw_refresh)

    async def _revoke_chain(self, session: AsyncSession, start: RefreshToken) -> None:
        """Revoke every token descended from `start`, plus any live sibling."""
        now = datetime.now(tz=UTC)
        # Simplest correct thing: revoke every unrevoked token for this user.
        # Walking `replaced_by_id` would leave tokens minted by a parallel login
        # untouched, and after a confirmed leak we want none of them alive.
        rows = (
            await session.execute(
                select(RefreshToken).where(
                    RefreshToken.user_id == start.user_id,
                    RefreshToken.revoked_at.is_(None),
                )
            )
        ).scalars()
        for row in rows:
            row.revoked_at = now

    # ── devices ───────────────────────────────────────────────────────────

    async def register_device(
        self, session: AsyncSession, user_id: uuid_lib.UUID, payload: DeviceRequest
    ) -> None:
        existing = (
            await session.execute(
                select(Device).where(
                    Device.user_id == user_id, Device.device_id == payload.device_id
                )
            )
        ).scalar_one_or_none()

        if existing is not None:
            existing.last_seen_at = datetime.now(tz=UTC)
        else:
            session.add(Device(user_id=user_id, device_id=payload.device_id))
        await session.commit()

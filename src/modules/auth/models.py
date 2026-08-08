import uuid as uuid_lib
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import CITEXT, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.shared.base_model import Base, TimestampMixin


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid_lib.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid_lib.uuid4
    )
    # CITEXT rather than lowercasing in Python: the uniqueness guarantee then
    # lives in the database, so no code path can create "A@b.com" alongside
    # "a@b.com" by forgetting to normalise first.
    email: Mapped[str] = mapped_column(CITEXT, unique=True, nullable=False)
    # Google's stable subject identifier. This, not the email, is the identity:
    # a user can change their Google email address and must keep their account.
    #
    # `unique=True, index=True` renders as one unique INDEX, matching
    # RefreshToken.token_hash below — a combination migration 0001 already
    # proved produces no autogenerate drift. A named UniqueConstraint is where
    # Alembic's name matching turns ambiguous, and test_migrations.py checks.
    google_sub: Mapped[str] = mapped_column(
        String(255), unique=True, index=True, nullable=False
    )
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[uuid_lib.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid_lib.uuid4
    )
    user_id: Mapped[uuid_lib.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Stored hashed for the same reason passwords are: a database dump must not
    # hand out usable 30-day credentials.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # The rotation chain. When a revoked token is presented again we walk this
    # to revoke every descendant — the presenter and the legitimate holder
    # cannot both be trusted, so neither keeps access.
    replaced_by_id: Mapped[uuid_lib.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("refresh_tokens.id", ondelete="SET NULL"), default=None
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Device(Base):
    __tablename__ = "devices"
    __table_args__ = (UniqueConstraint("user_id", "device_id", name="uq_devices_user_device"),)

    id: Mapped[uuid_lib.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid_lib.uuid4
    )
    user_id: Mapped[uuid_lib.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Opaque text. The client mints this as 32 hex chars from Math.random(),
    # not a UUID, so it is never parsed here.
    device_id: Mapped[str] = mapped_column(String(128), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

from datetime import UTC, datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base. Every model's metadata lands here for Alembic."""


def utcnow() -> datetime:
    return datetime.now(tz=UTC)


class TimestampMixin:
    """Server-owned `created_at` / `updated_at`.

    Deliberately *not* applied to `transactions`: that table's timestamps are
    client-owned (they come off the device and drive last-write-wins), so a
    server-side `onupdate` would overwrite the very value the conflict
    resolution depends on.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

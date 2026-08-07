import uuid as uuid_lib
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Sequence,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.shared.base_model import Base

# The cursor's source of truth. Every insert and every update to a transaction
# takes a fresh value from this sequence, and the client's cursor is the highest
# value it has seen.
#
# Not `updated_at`: client clocks are wrong, two rows can share a millisecond,
# and a row written inside a long transaction can commit *after* a row with a
# later timestamp — each of which silently drops rows from a timestamp-based
# pull. A sequence has none of those failure modes.
SYNC_SEQ = Sequence("transaction_sync_seq")


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        # Per user, not global. Two users could in principle generate the same
        # v4 UUID, and scoping the constraint costs nothing.
        UniqueConstraint("user_id", "uuid", name="uq_transactions_user_uuid"),
        # The pull query, exactly: WHERE user_id = ? AND sync_seq > ? ORDER BY sync_seq
        Index("ix_transactions_user_sync_seq", "user_id", "sync_seq"),
        Index("ix_transactions_user_occurred_at", "user_id", "occurred_at"),
    )

    id: Mapped[uuid_lib.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid_lib.uuid4
    )
    user_id: Mapped[uuid_lib.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # The client-generated v4 UUID. This is the row's identity across devices
    # and is never reassigned — the client's reparse path deliberately preserves
    # it so a synced id does not change under the server when the parser bumps.
    uuid: Mapped[str] = mapped_column(String(36), nullable=False)

    sync_seq: Mapped[int] = mapped_column(BigInteger, SYNC_SEQ, nullable=False)

    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)

    # BIGINT integer minor units. Never NUMERIC, never float, never divided to
    # "get the amount". A trailing full stop in a message once inflated an
    # amount 100x in this codebase; money arithmetic here is treated as
    # adversarial, and the server does no arithmetic on it at all.
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance_after_minor: Mapped[int | None] = mapped_column(BigInteger, default=None)

    # Always TZS today. Kept as a column rather than hardcoded.
    currency: Mapped[str] = mapped_column(String(8), nullable=False)

    counterparty: Mapped[str | None] = mapped_column(String(512), default=None)
    reference: Mapped[str | None] = mapped_column(String(128), default=None)

    # When the notification arrived — NOT a date parsed from the message text.
    # It can be wrong for a delayed notification, so nothing here may assume it
    # is the true transaction time.
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    category: Mapped[str | None] = mapped_column(String(32), default=None)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    parser_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)

    # The user corrected this row by hand. The client refuses to re-parse these,
    # and any server-side enrichment added later must skip them too.
    is_user_edited: Mapped[bool] = mapped_column(Boolean, nullable=False)

    # Client-owned, in epoch-ms on the wire. `updated_at` drives last-write-wins,
    # so it must never be touched server-side — no onupdate, no default.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Tombstone. Non-null means deleted. There is no hard DELETE path anywhere
    # in this service — a hard delete cannot propagate, so the row would simply
    # return from the other side on the next sync.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    # Deliberately absent, and must stay absent: `body`, `title`, `raw_text`,
    # `capture_id`, and the client's local rowid. Raw message text never leaves
    # the device; adding any of these is a privacy regression, not a feature.

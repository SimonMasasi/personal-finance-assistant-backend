"""transactions + the sync_seq sequence

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The cursor's source of truth. Bumped on every insert AND every update to
    # a transaction, so "give me everything above seq N" can never miss a row.
    op.execute("CREATE SEQUENCE IF NOT EXISTS transaction_sync_seq")

    op.create_table(
        "transactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # The client-generated v4 UUID: this row's identity across devices.
        sa.Column("uuid", sa.String(length=36), nullable=False),
        sa.Column(
            "sync_seq",
            sa.BigInteger(),
            server_default=sa.text("nextval('transaction_sync_seq')"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("direction", sa.String(length=8), nullable=False),
        # BIGINT integer minor units. Never NUMERIC, never float.
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("balance_after_minor", sa.BigInteger(), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("counterparty", sa.String(length=512), nullable=True),
        sa.Column("reference", sa.String(length=128), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("parser_version", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("is_user_edited", sa.Boolean(), nullable=False),
        # Client-owned. No server-side default or onupdate: `updated_at` drives
        # last-write-wins, so the server touching it would corrupt the ordering.
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        # Tombstone. There is no hard-delete path in this service.
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        # Unique per user, not globally: two users could in principle generate
        # the same v4 UUID, and scoping the constraint costs nothing.
        sa.UniqueConstraint("user_id", "uuid", name="uq_transactions_user_uuid"),
    )

    # Serves the pull query exactly: user_id = ? AND sync_seq > ? ORDER BY sync_seq
    op.create_index("ix_transactions_user_sync_seq", "transactions", ["user_id", "sync_seq"])
    # Ascending, not DESC. Postgres scans a btree backwards at the same cost, so
    # this serves "WHERE user_id = ? ORDER BY occurred_at DESC" just as well —
    # and a plain column index is one Alembic autogenerate can actually compare,
    # which keeps the models-vs-migrations drift test meaningful.
    op.create_index("ix_transactions_user_occurred_at", "transactions", ["user_id", "occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_transactions_user_occurred_at", table_name="transactions")
    op.drop_index("ix_transactions_user_sync_seq", table_name="transactions")
    op.drop_table("transactions")
    op.execute("DROP SEQUENCE IF EXISTS transaction_sync_seq")

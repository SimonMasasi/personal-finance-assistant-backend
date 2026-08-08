"""Replace password authentication with Google Sign-In.

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Order is load-bearing: the wipe has to precede the ADD COLUMN.

    Every existing account authenticated with a password and carries no Google
    identity that could be derived from its email, so there is nothing to
    backfill — the accounts go. That was an explicit decision, not a shortcut:
    these were test accounts, and the alternative (a nullable `google_sub` and
    a linking flow) is real machinery to carry forever for zero real users.

    DELETE rather than TRUNCATE ... CASCADE. DELETE honours exactly the
    ON DELETE CASCADE relationships declared in 0001 and 0002 — refresh_tokens,
    devices, transactions — whereas TRUNCATE CASCADE would silently empty any
    future table that merely happens to reference users. It is also
    transactional, so a failure below rolls the deletion back with it.
    """
    op.execute("DELETE FROM users")

    # Only legal because the table is now empty: Postgres refuses ADD COLUMN
    # NOT NULL with no server default on a table containing rows.
    op.add_column("users", sa.Column("google_sub", sa.String(length=255), nullable=False))
    op.create_index("ix_users_google_sub", "users", ["google_sub"], unique=True)

    op.drop_column("users", "password_hash")


def downgrade() -> None:
    """Symmetric, and deletes for the same reason.

    `password_hash` is NOT NULL with no server default, so a row could only
    survive this by being given an invented hash — and an invented hash is one
    Argon2 will never match. That is an account which exists and can never be
    signed into, which is worse than no account at all. Deleting is the honest
    inverse of a migration whose entire premise is that these users are gone.
    """
    op.execute("DELETE FROM users")

    op.add_column(
        "users", sa.Column("password_hash", sa.String(length=255), nullable=False)
    )
    op.drop_index("ix_users_google_sub", table_name="users")
    op.drop_column("users", "google_sub")

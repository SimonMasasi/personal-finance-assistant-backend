"""Migrations, exercised the way the working style in backend-prompt.md asks.

"Every schema change is an Alembic migration, and every migration must be tested
against a database that already contains rows — not just a fresh one."
"""

import asyncio
import subprocess
import sys
from collections.abc import Iterator

import asyncpg
import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from src.core.config import SETTINGS

# Imported for their side effect: each registers its tables on Base.metadata.
# Without them the metadata is empty and the comparison below reports every
# table as "should be dropped" rather than comparing anything.
from src.modules.auth.models import Device, RefreshToken, User  # noqa: F401,E402
from src.modules.transactions.models import Transaction  # noqa: F401,E402
from src.shared.base_model import Base
from src.tests.conftest import PROJECT_ROOT, _admin_dsn

pytestmark = pytest.mark.asyncio(loop_scope="session")

SCRATCH_DB = "pfa_migration_scratch"


def _scratch_url() -> str:
    base, _, _ = SETTINGS.DATABASE_URL.rpartition("/")
    return f"{base}/{SCRATCH_DB}"


async def _recreate_scratch_database() -> None:
    conn = await asyncpg.connect(_admin_dsn())
    try:
        # Terminate stragglers so DROP cannot block on an idle connection.
        await conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = $1 AND pid <> pg_backend_pid()",
            SCRATCH_DB,
        )
        await conn.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}"')
        await conn.execute(f'CREATE DATABASE "{SCRATCH_DB}"')
    finally:
        await conn.close()


def _alembic(*args: str) -> subprocess.CompletedProcess[str]:
    """Run Alembic against the scratch database."""
    import os

    env = {**os.environ, "DATABASE_URL": _scratch_url()}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        env=env,
    )


@pytest.fixture(scope="module")
def scratch_db() -> Iterator[None]:
    asyncio.run(_recreate_scratch_database())
    yield


class TestMigrationsAgainstPopulatedDatabase:
    async def test_migrations_apply_to_a_database_that_already_has_password_users(
        self, scratch_db: None
    ) -> None:
        """Stop at 0001, insert a real password-era row, then apply the rest.

        A migration that only ever runs against an empty database proves
        nothing about the upgrade every deployment after the first performs.
        """
        result = _alembic("upgrade", "0001")
        assert result.returncode == 0, result.stderr

        engine = create_async_engine(_scratch_url())
        try:
            async with engine.begin() as conn:
                # Valid at 0001, where password_hash still exists.
                await conn.execute(
                    text(
                        "INSERT INTO users (id, email, password_hash, display_name) "
                        "VALUES (gen_random_uuid(), 'legacy@example.com', 'x', 'Legacy')"
                    )
                )

            result = _alembic("upgrade", "head")
            assert result.returncode == 0, result.stderr

            async with engine.connect() as conn:
                # The password-era row is GONE, and that is the point of 0003,
                # not a bug: it has no Google identity and no way to obtain one,
                # so there is nothing it could ever sign in with again.
                survivors = await conn.execute(
                    text("SELECT count(*) FROM users WHERE email = 'legacy@example.com'")
                )
                assert survivors.scalar_one() == 0

                # And the new table is usable, sequence and all.
                seq = await conn.execute(text("SELECT nextval('transaction_sync_seq')"))
                assert seq.scalar_one() >= 1

            # The migrated schema actually enforces one account per Google
            # subject. Nothing else in the suite exercises this index against
            # real DDL rather than the ORM's idea of it.
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO users (id, email, google_sub, display_name) "
                        "VALUES (gen_random_uuid(), 'a@example.com', 'sub-1', 'A')"
                    )
                )
            with pytest.raises(IntegrityError):
                async with engine.begin() as conn:
                    await conn.execute(
                        text(
                            "INSERT INTO users (id, email, google_sub, display_name) "
                            "VALUES (gen_random_uuid(), 'b@example.com', 'sub-1', 'B')"
                        )
                    )
        finally:
            await engine.dispose()

    async def test_downgrade_then_upgrade_round_trips(self, scratch_db: None) -> None:
        """Every migration's downgrade must actually undo its upgrade."""
        assert _alembic("upgrade", "head").returncode == 0

        down = _alembic("downgrade", "base")
        assert down.returncode == 0, down.stderr

        up = _alembic("upgrade", "head")
        assert up.returncode == 0, up.stderr


class TestSchemaMatchesModels:
    async def test_no_pending_autogenerate_diff(self) -> None:
        """The models and the migrations agree.

        This is the test that catches the most common real mistake: adding a
        column to a model and forgetting the migration. It runs against the
        migrated test database, so a mismatch means the two have drifted.
        """
        engine = create_async_engine(SETTINGS.DATABASE_URL)
        try:
            async with engine.connect() as conn:
                diffs = await conn.run_sync(
                    lambda sync_conn: compare_metadata(
                        MigrationContext.configure(sync_conn), Base.metadata
                    )
                )
        finally:
            await engine.dispose()

        # Server-default differences are noise here: `sync_seq` renders its
        # nextval() default differently through the ORM than Postgres reports
        # it, and timestamp defaults compare textually. Structural differences
        # — a missing table, column, index or constraint — are what matter.
        structural = [d for d in diffs if "default" not in str(d[0])]
        assert not structural, f"models and migrations have drifted: {structural}"


class TestMigrationIdempotence:
    async def test_upgrade_head_twice_is_a_noop(self, scratch_db: None) -> None:
        assert _alembic("upgrade", "head").returncode == 0
        second = _alembic("upgrade", "head")
        assert second.returncode == 0, second.stderr

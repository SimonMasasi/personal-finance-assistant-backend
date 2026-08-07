import asyncio
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from config import SETTINGS

PROJECT_ROOT = Path(__file__).resolve().parents[2]

TEST_PASSWORD = "Test@1234"

# Tables the suite empties between tests. `alembic_version` is excluded on
# purpose — wiping it would make the next test re-run every migration.
_TRUNCATE_TABLES = "transactions, refresh_tokens, devices, users"


def _admin_dsn() -> str:
    """A plain asyncpg DSN pointing at the maintenance database."""
    url = SETTINGS.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    base, _, _ = url.rpartition("/")
    return f"{base}/postgres"


def _test_db_name() -> str:
    return SETTINGS.DATABASE_URL.rsplit("/", 1)[-1]


async def _create_database_if_missing() -> None:
    conn = await asyncpg.connect(_admin_dsn())
    try:
        name = _test_db_name()
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", name)
        if not exists:
            # CREATE DATABASE cannot be parameterised or run in a transaction.
            await conn.execute(f'CREATE DATABASE "{name}"')
    finally:
        await conn.close()


@pytest.fixture(scope="session", autouse=True)
def _migrated_database() -> Iterator[None]:
    """Create the test database and bring it to head, once per session.

    Deliberately *not* `SQLModel.metadata.create_all`-style table creation: the
    suite must exercise the same DDL production runs, including the citext
    extension and the transaction_sync_seq sequence, neither of which the ORM
    metadata would produce on its own.

    This fixture is synchronous so it can call `asyncio.run` and shell out to
    Alembic (whose env.py calls `asyncio.run` itself, and so cannot be invoked
    from inside a running loop).
    """
    asyncio.run(_create_database_if_missing())

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"alembic upgrade failed:\n{result.stdout}\n{result.stderr}")

    yield


@pytest_asyncio.fixture(loop_scope="session", autouse=True)
async def clean_db(_migrated_database: None) -> AsyncIterator[None]:
    """Empty every table before each test.

    TRUNCATE rather than DROP/CREATE so the schema (and the migration history)
    survives. RESTART IDENTITY does not touch transaction_sync_seq, which is a
    standalone sequence — that is fine and in fact desirable: cursors staying
    monotonic across tests catches any code that assumes they start at zero.
    """
    from src.shared.database import engine

    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {_TRUNCATE_TABLES} RESTART IDENTITY CASCADE"))
    yield


@pytest_asyncio.fixture(loop_scope="session")
async def client() -> AsyncIterator[AsyncClient]:
    from app import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def register_user(
    client: AsyncClient, email: str, display_name: str = "Test User"
) -> dict[str, str]:
    """Register and return {access, refresh, user_id}."""
    response = await client.post(
        "/v1/auth/register",
        json={"email": email, "password": TEST_PASSWORD, "display_name": display_name},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return {
        "access": body["access"],
        "refresh": body["refresh"],
        "user_id": body["user"]["id"],
    }


@pytest_asyncio.fixture(loop_scope="session")
async def user_a(client: AsyncClient) -> dict[str, str]:
    return await register_user(client, "alice@example.com", "Alice")


@pytest_asyncio.fixture(loop_scope="session")
async def user_b(client: AsyncClient) -> dict[str, str]:
    return await register_user(client, "bob@example.com", "Bob")


def auth_headers(session: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {session['access']}"}

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.core.config import SETTINGS

engine = create_async_engine(
    SETTINGS.DATABASE_URL,
    echo=False,
    pool_pre_ping=True,  # mobile traffic is bursty; stale pooled connections are common
)

# expire_on_commit=False so ORM objects stay readable after the request's
# commit — otherwise serializing a response would trigger a lazy reload against
# a session that is already done.
SessionFactory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """One session per request.

    No commit here. Services own their transaction boundaries — the sync
    endpoint in particular needs its push and pull inside a single explicit
    transaction, which an implicit commit-on-exit would obscure.
    """
    async with SessionFactory() as session:
        yield session


# There is deliberately no `create_all()` anywhere in this codebase. Every
# schema change is an Alembic migration, run explicitly by compose and CI.

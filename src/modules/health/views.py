from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.shared.database import get_session

health_router = APIRouter(tags=["health"])


@health_router.get("/health")
async def health() -> dict[str, str]:
    """Liveness. Answers as long as the process is serving."""
    return {"status": "ok"}


@health_router.get("/health/ready")
async def readiness(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    """Readiness. Separate from liveness so a database blip restarts nothing."""
    await session.execute(text("SELECT 1"))
    return {"status": "ok", "database": "ok"}

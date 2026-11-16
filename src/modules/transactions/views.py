from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.auth.dependencies import CurrentUserId
from src.modules.transactions.dtos import SyncRequest, SyncResponse
from src.modules.transactions.services import TransactionsService
from src.shared.database import get_session

transactions_router = APIRouter(prefix="/v1", tags=["sync"])
transactions_service = TransactionsService()

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@transactions_router.post("/sync", response_model=SyncResponse)
async def sync(
    payload: SyncRequest, user_id: CurrentUserId, session: SessionDep
) -> SyncResponse:
    """Delta sync. One round trip does both directions.

    Safe to retry: the client syncs over a mobile network and will resend on
    timeout, quite possibly after the server already committed.
    """
    return await transactions_service.sync(session, user_id, payload)

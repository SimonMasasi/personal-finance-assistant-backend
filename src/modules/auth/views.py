from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import SETTINGS
from src.modules.auth.dependencies import CurrentUserId
from src.modules.auth.dtos import (
    AuthResponse,
    DeviceRequest,
    GoogleSignInRequest,
    RefreshRequest,
    TokenPairResponse,
)
from src.modules.auth.services import AuthService
from src.shared.database import get_session
from src.shared.rate_limit import limiter

auth_router = APIRouter(prefix="/v1", tags=["auth"])
auth_service = AuthService()

SessionDep = Annotated[AsyncSession, Depends(get_session)]


# Every rate-limited handler must accept both `request` (slowapi reads the
# client address off it) and `response` (it writes the X-RateLimit-* headers
# onto it), even when the body is all the handler itself needs.
#
# One endpoint for sign-up and sign-in both, always 200 — see the docstring on
# `AuthService.google_sign_in` for why the status must not vary.
@auth_router.post("/auth/google", response_model=AuthResponse)
@limiter.limit(SETTINGS.RATE_LIMIT_GOOGLE)
async def google_sign_in(
    request: Request, response: Response, payload: GoogleSignInRequest, session: SessionDep
) -> AuthResponse:
    return await auth_service.google_sign_in(session, payload)


@auth_router.post("/auth/refresh", response_model=TokenPairResponse)
@limiter.limit(SETTINGS.RATE_LIMIT_REFRESH)
async def refresh(
    request: Request, response: Response, payload: RefreshRequest, session: SessionDep
) -> TokenPairResponse:
    return await auth_service.refresh(session, payload)


@auth_router.post("/devices", status_code=status.HTTP_204_NO_CONTENT)
async def register_device(
    payload: DeviceRequest, user_id: CurrentUserId, session: SessionDep
) -> Response:
    await auth_service.register_device(session, user_id, payload)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from config import SETTINGS
from src.modules.auth.dtos import (
    AuthResponse,
    DeviceRequest,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenPairResponse,
)
from src.modules.auth.services import AuthService
from src.shared.database import get_session
from src.shared.dependencies import CurrentUserId
from src.shared.rate_limit import limiter

auth_router = APIRouter(prefix="/v1", tags=["auth"])
auth_service = AuthService()

SessionDep = Annotated[AsyncSession, Depends(get_session)]


# Every rate-limited handler must accept both `request` (slowapi reads the
# client address off it) and `response` (it writes the X-RateLimit-* headers
# onto it), even when the body is all the handler itself needs.
@auth_router.post(
    "/auth/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED
)
@limiter.limit(SETTINGS.RATE_LIMIT_REGISTER)
async def register(
    request: Request, response: Response, payload: RegisterRequest, session: SessionDep
) -> AuthResponse:
    return await auth_service.register(session, payload)


@auth_router.post("/auth/login", response_model=AuthResponse)
@limiter.limit(SETTINGS.RATE_LIMIT_LOGIN)
async def login(
    request: Request, response: Response, payload: LoginRequest, session: SessionDep
) -> AuthResponse:
    return await auth_service.login(session, payload)


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

"""Domain errors and the handlers that turn them into HTTP responses.

The reference project (meeting-assistant-backend) returns HTTP 200 with a
`status: false` envelope for every failure. This service does not: the client is
a phone on a mobile network that must distinguish "your token expired, refresh
and retry" (401) from "your batch was too big, split it" (413) from "server
broke, retry with backoff" (500). Those need to be status codes, not a field.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Base for errors that map to a deliberate HTTP response."""

    status_code = 500
    code = "internal_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class AuthError(AppError):
    status_code = 401
    code = "unauthorized"


class ForbiddenError(AppError):
    status_code = 403
    code = "forbidden"


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class PayloadTooLargeError(AppError):
    status_code = 413
    code = "payload_too_large"


class ValidationError(AppError):
    status_code = 422
    code = "invalid_input"


class ServiceUnavailableError(AppError):
    """An upstream we depend on is down — currently only Google's key endpoint.

    503 rather than a bare 500, and emphatically not 401. The client already
    treats `status >= 500` as retryable and 401 as "sign the user out"
    (src/services/http.ts). Reporting a Google outage as 401 would sign out
    every user for the duration of it; as 503 they back off and retry, which is
    what the situation actually calls for.
    """

    status_code = 503
    code = "service_unavailable"


def _body(code: str, message: str) -> dict[str, dict[str, str]]:
    return {"error": {"code": code, "message": message}}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if isinstance(exc, AuthError) else None
        return JSONResponse(
            status_code=exc.status_code,
            content=_body(exc.code, exc.message),
            headers=headers,
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
        # Log the traceback, return nothing about it. An exception message can
        # contain a SQL fragment with a counterparty name in it.
        logger.exception("Unhandled error: %s", type(exc).__name__)
        return JSONResponse(
            status_code=500,
            content=_body("internal_error", "An unexpected error occurred."),
        )

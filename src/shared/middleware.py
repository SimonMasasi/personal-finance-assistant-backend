from collections.abc import Awaitable, Callable

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from config import SETTINGS


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject an oversized body before anything parses it.

    Pydantic's `max_length` on `changes` also caps the batch, but only *after*
    the whole request has been read and deserialized into Python objects. A
    100 MB body would be fully materialised before that check ran. Refusing on
    Content-Length costs nothing and happens first.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > SETTINGS.MAX_REQUEST_BYTES:
                    return JSONResponse(
                        status_code=413,
                        content={
                            "error": {
                                "code": "payload_too_large",
                                "message": (
                                    f"Request body exceeds "
                                    f"{SETTINGS.MAX_REQUEST_BYTES} bytes."
                                ),
                            }
                        },
                    )
            except ValueError:
                return JSONResponse(
                    status_code=400,
                    content={
                        "error": {
                            "code": "invalid_request",
                            "message": "Malformed Content-Length header.",
                        }
                    },
                )
        return await call_next(request)

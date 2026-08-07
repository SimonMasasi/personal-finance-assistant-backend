from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from config import SETTINGS
from src.shared.errors import register_error_handlers
from src.shared.logging import configure_logging
from src.shared.middleware import BodySizeLimitMiddleware
from src.shared.rate_limit import limiter
from src.shared.routes import routes
from src.utils.open_api_routes import custom_openapi
from src.utils.startup_banner import log_startup_banner


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging(SETTINGS.DEBUG)
    log_startup_banner()
    yield


app = FastAPI(
    title=SETTINGS.APP_NAME,
    lifespan=lifespan,
    docs_url="/docs" if SETTINGS.DEBUG else None,
)

# Unlike the reference project, migrations are NOT run at import time here.
# Every schema change must be an Alembic migration that was tested against a
# database containing rows; running them implicitly on boot makes it easy to
# never notice one is missing. Compose and CI run `alembic upgrade head`.

app.state.limiter = limiter
# slowapi's handler is typed for its own exception rather than the base
# Exception that Starlette's signature declares.
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]

app.add_middleware(SlowAPIMiddleware)
app.add_middleware(BodySizeLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in SETTINGS.CORS_ALLOW_ORIGINS.split(",")],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_error_handlers(app)

for router in routes:
    app.include_router(router)

app.openapi = lambda: custom_openapi(app)  # type: ignore[method-assign]


if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host=SETTINGS.APP_HOST,
        port=SETTINGS.APP_PORT,
        log_level="debug" if SETTINGS.DEBUG else "info",
        reload=SETTINGS.DEBUG,
    )

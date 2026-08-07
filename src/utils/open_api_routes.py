from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.openapi.utils import get_openapi
from scalar_fastapi import get_scalar_api_reference

open_api_router = APIRouter(tags=["docs"])


def custom_openapi(app: FastAPI) -> dict[str, Any]:
    """Inject the bearer scheme so the docs UI can actually authenticate."""
    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(
        title="Personal Finance Assistant API",
        version="1.0.0",
        description=(
            "Sync server for the PersonalFinanceAssistant Android app.\n\n"
            "Only transactions sync. There is no endpoint that accepts raw "
            "notification text, by design."
        ),
        routes=app.routes,
    )
    schema.setdefault("components", {})["securitySchemes"] = {
        "BearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
    }
    schema["security"] = [{"BearerAuth": []}]

    app.openapi_schema = schema
    return schema


@open_api_router.get("/scalar", include_in_schema=False)
async def scalar_docs() -> Any:
    return get_scalar_api_reference(
        openapi_url="/openapi.json",
        title="Personal Finance Assistant API",
    )

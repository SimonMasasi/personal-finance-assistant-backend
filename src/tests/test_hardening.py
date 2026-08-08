"""Milestone 4: health, limits, logging redaction, and the privacy invariant."""

from typing import cast

import pytest
from httpx import AsyncClient

from config import SETTINGS
from src.tests.conftest import auth_headers, register_user

pytestmark = pytest.mark.asyncio(loop_scope="session")


class TestHealth:
    async def test_liveness(self, client: AsyncClient) -> None:
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    async def test_readiness_touches_the_database(self, client: AsyncClient) -> None:
        response = await client.get("/health/ready")
        assert response.status_code == 200
        assert response.json()["database"] == "ok"

    async def test_health_needs_no_auth(self, client: AsyncClient) -> None:
        assert (await client.get("/health")).status_code == 200


class TestBodySizeLimit:
    async def test_oversized_body_is_refused_before_parsing(
        self, client: AsyncClient, user_a: dict[str, str]
    ) -> None:
        oversized = "x" * (SETTINGS.MAX_REQUEST_BYTES + 1024)
        response = await client.post(
            "/v1/sync",
            content=oversized,
            headers={**auth_headers(user_a), "Content-Type": "application/json"},
        )
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "payload_too_large"


class TestRateLimitConfiguration:
    """The suite raises the limits so it can run, so this asserts the wiring
    rather than trying to trip a limiter that has been deliberately loosened."""

    async def test_auth_routes_are_decorated_with_a_limit(self) -> None:
        import app as _app  # noqa: F401  — importing registers the routes
        from src.shared.rate_limit import limiter

        registered = set(limiter._route_limits)
        for handler in ("google_sign_in", "refresh"):
            key = f"src.modules.auth.views.{handler}"
            assert key in registered, f"{handler} is not rate limited"

    async def test_sync_is_not_rate_limited(self) -> None:
        """Deliberate. A client draining a backlog issues many syncs in a row,
        and the batch cap plus the body-size limit already bound the work."""
        import app as _app  # noqa: F401
        from src.shared.rate_limit import limiter

        assert "src.modules.sync.views.sync" not in set(limiter._route_limits)

    async def test_limiter_is_enabled(self) -> None:
        from src.shared.rate_limit import limiter

        assert limiter.enabled


class TestErrorShape:
    async def test_errors_use_the_documented_envelope(self, client: AsyncClient) -> None:
        response = await client.post("/v1/sync", json={"cursor": "0", "changes": []})
        assert response.status_code == 401

        body = response.json()
        assert set(body["error"]) == {"code", "message"}
        assert body["error"]["code"] == "unauthorized"

    async def test_401_carries_www_authenticate(self, client: AsyncClient) -> None:
        response = await client.post("/v1/sync", json={"cursor": "0", "changes": []})
        assert response.headers.get("www-authenticate") == "Bearer"


class TestPrivacyInvariant:
    async def test_no_endpoint_accepts_raw_message_text(self) -> None:
        """A structural check, not a behavioural one.

        The guarantee is that no request body anywhere in the API has a field
        that could carry a notification body. Asserting it against the OpenAPI
        schema means a future endpoint that adds one fails this test.
        """
        from app import app

        schema = app.openapi()
        forbidden = {"body", "title", "raw_text", "message", "text", "capture_id"}

        offenders = [
            f"{name}.{field}"
            for name, model in schema.get("components", {}).get("schemas", {}).items()
            for field in model.get("properties", {})
            if field in forbidden
        ]
        assert not offenders, f"raw-text-capable fields exposed: {offenders}"

    async def test_transactions_table_has_no_text_columns(self) -> None:
        from src.modules.sync.models import Transaction

        columns = set(Transaction.__table__.columns.keys())
        assert not (columns & {"body", "title", "raw_text", "capture_id"})


class TestOpenApi:
    async def test_scalar_docs_render(self, client: AsyncClient) -> None:
        assert (await client.get("/scalar")).status_code == 200

    async def test_openapi_declares_bearer_auth(self, client: AsyncClient) -> None:
        schema = (await client.get("/openapi.json")).json()
        assert "BearerAuth" in schema["components"]["securitySchemes"]


class TestTenantScopingIsStructural:
    async def test_sync_queries_are_keyed_on_user_id(self) -> None:
        """user_id must be part of the primary lookup, not a filter added later.

        Enforced here by asserting the unique constraint and the pull index both
        lead with user_id — the schema is what makes a cross-tenant read
        impossible, not the discipline of every future query author.
        """
        from sqlalchemy import Table, UniqueConstraint

        from src.modules.sync.models import Transaction

        # __table__ is typed as FromClause on the declarative base; the concrete
        # Table is what carries .constraints and .indexes.
        table = cast(Table, Transaction.__table__)

        unique = next(
            c
            for c in table.constraints
            if isinstance(c, UniqueConstraint) and c.name == "uq_transactions_user_uuid"
        )
        assert [c.name for c in unique.columns] == ["user_id", "uuid"]

        pull_index = next(i for i in table.indexes if i.name == "ix_transactions_user_sync_seq")
        assert [c.name for c in pull_index.columns][0] == "user_id"


class TestRegistrationFlowEndToEnd:
    async def test_register_then_device_then_sync(self, client: AsyncClient) -> None:
        """The exact sequence the client performs on first sign-in."""
        session = await register_user(client, "flow@example.com", "Flow User")

        device = await client.post(
            "/v1/devices",
            json={"device_id": "f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0"},
            headers=auth_headers(session),
        )
        assert device.status_code == 204

        synced = await client.post(
            "/v1/sync", json={"cursor": "0", "changes": []}, headers=auth_headers(session)
        )
        assert synced.status_code == 200
        assert synced.json()["cursor"] == "0"

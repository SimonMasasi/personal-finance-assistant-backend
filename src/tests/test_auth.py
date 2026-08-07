import pytest
from httpx import AsyncClient

from src.tests.conftest import TEST_PASSWORD, auth_headers, register_user

pytestmark = pytest.mark.asyncio(loop_scope="session")

REGISTER_URL = "/v1/auth/register"
LOGIN_URL = "/v1/auth/login"
REFRESH_URL = "/v1/auth/refresh"
DEVICES_URL = "/v1/devices"


class TestRegister:
    async def test_register_returns_tokens_and_user(self, client: AsyncClient) -> None:
        response = await client.post(
            REGISTER_URL,
            json={
                "email": "new@example.com",
                "password": TEST_PASSWORD,
                "display_name": "New User",
            },
        )
        assert response.status_code == 201
        body = response.json()

        assert body["access"] and body["refresh"]
        # snake_case, per the wire-format decision. The client's AuthUser type
        # uses displayName and needs updating — see the README.
        assert body["user"]["email"] == "new@example.com"
        assert body["user"]["display_name"] == "New User"
        assert "password" not in body["user"]
        assert "password_hash" not in body["user"]

    async def test_duplicate_email_is_conflict(self, client: AsyncClient) -> None:
        payload = {
            "email": "dupe@example.com",
            "password": TEST_PASSWORD,
            "display_name": "Dupe",
        }
        assert (await client.post(REGISTER_URL, json=payload)).status_code == 201
        second = await client.post(REGISTER_URL, json=payload)
        assert second.status_code == 409

    async def test_duplicate_email_is_case_insensitive(self, client: AsyncClient) -> None:
        """CITEXT: Alice@... and alice@... are the same account."""
        await client.post(
            REGISTER_URL,
            json={"email": "Case@Example.com", "password": TEST_PASSWORD, "display_name": "A"},
        )
        second = await client.post(
            REGISTER_URL,
            json={"email": "case@example.com", "password": TEST_PASSWORD, "display_name": "B"},
        )
        assert second.status_code == 409

    async def test_login_is_case_insensitive(self, client: AsyncClient) -> None:
        await client.post(
            REGISTER_URL,
            json={"email": "Mixed@Example.com", "password": TEST_PASSWORD, "display_name": "M"},
        )
        response = await client.post(
            LOGIN_URL, json={"email": "mixed@example.com", "password": TEST_PASSWORD}
        )
        assert response.status_code == 200

    @pytest.mark.parametrize("password", ["short1", "nodigitshere", "12345678"])
    async def test_weak_password_is_rejected(
        self, client: AsyncClient, password: str
    ) -> None:
        response = await client.post(
            REGISTER_URL,
            json={"email": "weak@example.com", "password": password, "display_name": "W"},
        )
        assert response.status_code == 422

    async def test_invalid_email_is_rejected(self, client: AsyncClient) -> None:
        response = await client.post(
            REGISTER_URL,
            json={"email": "not-an-email", "password": TEST_PASSWORD, "display_name": "X"},
        )
        assert response.status_code == 422


class TestLogin:
    async def test_login_succeeds(self, client: AsyncClient) -> None:
        await register_user(client, "login@example.com")
        response = await client.post(
            LOGIN_URL, json={"email": "login@example.com", "password": TEST_PASSWORD}
        )
        assert response.status_code == 200
        assert response.json()["access"]

    async def test_wrong_password_is_401(self, client: AsyncClient) -> None:
        await register_user(client, "wrongpw@example.com")
        response = await client.post(
            LOGIN_URL, json={"email": "wrongpw@example.com", "password": "Wrong@9999"}
        )
        assert response.status_code == 401

    async def test_unknown_email_gives_the_same_error_as_a_wrong_password(
        self, client: AsyncClient
    ) -> None:
        """Identical status and message, so login cannot enumerate accounts."""
        await register_user(client, "known@example.com")

        wrong_password = await client.post(
            LOGIN_URL, json={"email": "known@example.com", "password": "Wrong@9999"}
        )
        unknown_email = await client.post(
            LOGIN_URL, json={"email": "nobody@example.com", "password": "Wrong@9999"}
        )

        assert wrong_password.status_code == unknown_email.status_code == 401
        assert wrong_password.json() == unknown_email.json()


class TestRefreshRotation:
    async def test_refresh_issues_a_new_pair(self, client: AsyncClient) -> None:
        session = await register_user(client, "rot@example.com")
        response = await client.post(REFRESH_URL, json={"refresh": session["refresh"]})

        assert response.status_code == 200
        body = response.json()
        assert body["access"] and body["refresh"]
        assert body["refresh"] != session["refresh"], "refresh token must rotate"

    async def test_old_refresh_token_stops_working(self, client: AsyncClient) -> None:
        session = await register_user(client, "rot2@example.com")
        first = await client.post(REFRESH_URL, json={"refresh": session["refresh"]})
        assert first.status_code == 200

        replayed = await client.post(REFRESH_URL, json={"refresh": session["refresh"]})
        assert replayed.status_code == 401

    async def test_reuse_revokes_the_whole_chain(self, client: AsyncClient) -> None:
        """Presenting a spent token means it leaked; every live token dies.

        We cannot tell the thief from the legitimate holder, so neither keeps
        access and both must re-authenticate.
        """
        session = await register_user(client, "rot3@example.com")

        rotated = await client.post(REFRESH_URL, json={"refresh": session["refresh"]})
        current = rotated.json()["refresh"]

        # The attacker replays the spent token.
        replay = await client.post(REFRESH_URL, json={"refresh": session["refresh"]})
        assert replay.status_code == 401

        # The token the legitimate holder was using is now dead too.
        assert (await client.post(REFRESH_URL, json={"refresh": current})).status_code == 401

    async def test_garbage_refresh_token_is_401(self, client: AsyncClient) -> None:
        response = await client.post(REFRESH_URL, json={"refresh": "not-a-real-token"})
        assert response.status_code == 401

    async def test_access_token_is_not_accepted_as_a_refresh_token(
        self, client: AsyncClient
    ) -> None:
        session = await register_user(client, "rot4@example.com")
        response = await client.post(REFRESH_URL, json={"refresh": session["access"]})
        assert response.status_code == 401


class TestTokenTypes:
    async def test_refresh_token_is_rejected_as_a_bearer_token(
        self, client: AsyncClient
    ) -> None:
        """Otherwise the 30-day token would grant API access, defeating the
        short access-token lifetime entirely."""
        session = await register_user(client, "types@example.com")
        response = await client.post(
            "/v1/sync",
            json={"cursor": "0", "changes": []},
            headers={"Authorization": f"Bearer {session['refresh']}"},
        )
        assert response.status_code == 401

    async def test_missing_and_malformed_tokens_are_401(self, client: AsyncClient) -> None:
        for headers in ({}, {"Authorization": "Bearer nonsense"}, {"Authorization": "Basic x"}):
            response = await client.post(
                "/v1/sync", json={"cursor": "0", "changes": []}, headers=headers
            )
            assert response.status_code == 401, headers

    async def test_token_signed_with_another_key_is_rejected(
        self, client: AsyncClient
    ) -> None:
        import jwt

        forged = jwt.encode(
            {
                "sub": "00000000-0000-0000-0000-000000000000",
                "iat": 1_786_000_000,
                "exp": 4_102_444_800,
                "iss": "personal-finance-assistant-backend",
                "type": "access",
            },
            "a-different-secret-key-entirely-and-long-enough-for-hs256",
            algorithm="HS256",
        )
        response = await client.post(
            "/v1/sync",
            json={"cursor": "0", "changes": []},
            headers={"Authorization": f"Bearer {forged}"},
        )
        assert response.status_code == 401


class TestDevices:
    async def test_register_device_returns_204(self, client: AsyncClient) -> None:
        session = await register_user(client, "dev@example.com")
        response = await client.post(
            DEVICES_URL,
            json={"device_id": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"},
            headers=auth_headers(session),
        )
        assert response.status_code == 204

    async def test_registering_the_same_device_twice_is_fine(
        self, client: AsyncClient
    ) -> None:
        """The client calls this on every login, not just the first."""
        session = await register_user(client, "dev2@example.com")
        payload = {"device_id": "deadbeefdeadbeefdeadbeefdeadbeef"}
        for _ in range(2):
            response = await client.post(
                DEVICES_URL, json=payload, headers=auth_headers(session)
            )
            assert response.status_code == 204

    async def test_device_registration_requires_auth(self, client: AsyncClient) -> None:
        response = await client.post(DEVICES_URL, json={"device_id": "abc"})
        assert response.status_code == 401

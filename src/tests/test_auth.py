import pytest
from httpx import AsyncClient

from src.tests.conftest import auth_headers, google_token, register_user

pytestmark = pytest.mark.asyncio(loop_scope="session")

GOOGLE_URL = "/v1/auth/google"
REFRESH_URL = "/v1/auth/refresh"
DEVICES_URL = "/v1/devices"


class TestGoogleSignIn:
    """The verifier is stubbed here (see conftest); signature checking lives in
    unit/test_google_auth.py. These cover what happens once a token is trusted."""

    async def test_first_sign_in_creates_the_account(self, client: AsyncClient) -> None:
        response = await client.post(
            GOOGLE_URL, json={"id_token": google_token("new@example.com", display_name="New User")}
        )
        assert response.status_code == 200
        body = response.json()

        assert body["access"] and body["refresh"]
        # snake_case on the wire; the client maps display_name -> displayName
        # at its adapter boundary and nowhere else.
        assert body["user"]["email"] == "new@example.com"
        assert body["user"]["display_name"] == "New User"
        # Nothing about credentials should ever appear here.
        assert "password" not in body["user"]
        assert "id_token" not in body["user"]
        assert "google_sub" not in body["user"]

    async def test_signing_in_again_reuses_the_same_account(
        self, client: AsyncClient
    ) -> None:
        """The invariant the old duplicate-email conflict used to carry."""
        first = await register_user(client, "repeat@example.com")
        second = await register_user(client, "repeat@example.com")

        assert first["user_id"] == second["user_id"]
        # ...but a fresh session each time.
        assert first["refresh"] != second["refresh"]

    async def test_the_subject_is_the_identity_not_the_email(
        self, client: AsyncClient
    ) -> None:
        """A user can change their Google address and must keep their account —
        and with it, every transaction they have ever synced."""
        before = await register_user(client, "old@example.com", sub="stable-sub")
        after = await register_user(client, "renamed@example.com", sub="stable-sub")

        assert before["user_id"] == after["user_id"]

        me = await client.post(
            GOOGLE_URL, json={"id_token": google_token("renamed@example.com", sub="stable-sub")}
        )
        assert me.json()["user"]["email"] == "renamed@example.com"

    async def test_a_second_google_account_cannot_claim_a_taken_email(
        self, client: AsyncClient
    ) -> None:
        """Silently reassigning the row would hand one person another's data."""
        await register_user(client, "taken@example.com", sub="sub-one")

        response = await client.post(
            GOOGLE_URL, json={"id_token": google_token("taken@example.com", sub="sub-two")}
        )
        assert response.status_code == 409

    async def test_an_unverifiable_token_is_401(self, client: AsyncClient) -> None:
        response = await client.post(GOOGLE_URL, json={"id_token": "not-a-real-token"})
        assert response.status_code == 401

    async def test_the_body_accepts_nothing_but_the_token(
        self, client: AsyncClient
    ) -> None:
        """extra="forbid": everything about the user comes from the verified
        token, so a body trying to supply an email must be refused outright."""
        response = await client.post(
            GOOGLE_URL,
            json={
                "id_token": google_token("honest@example.com"),
                "email": "attacker@example.com",
            },
        )
        assert response.status_code == 422

    async def test_case_differing_emails_are_one_account(
        self, client: AsyncClient
    ) -> None:
        """CITEXT still earns its place: Google normalises addresses, but the
        database guarantee is what stops two rows existing."""
        await register_user(client, "Case@Example.com", sub="sub-case")
        response = await client.post(
            GOOGLE_URL, json={"id_token": google_token("case@example.com", sub="sub-other")}
        )
        assert response.status_code == 409


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

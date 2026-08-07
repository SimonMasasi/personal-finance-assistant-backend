"""The ten acceptance criteria from backend-prompt.md, one test each.

These are the behaviours that actually matter for a sync protocol; a passing
CRUD suite that skips them proves nothing.
"""

import uuid as uuid_lib
from typing import Any

import pytest
from httpx import AsyncClient

from src.tests.conftest import auth_headers, register_user

pytestmark = pytest.mark.asyncio(loop_scope="session")

SYNC_URL = "/v1/sync"

BASE_MS = 1_786_000_000_000


def make_tx(**overrides: Any) -> dict[str, Any]:
    """A transaction in exactly the shape the client's SQLite row has."""
    tx: dict[str, Any] = {
        "uuid": str(uuid_lib.uuid4()),
        "provider": "mpesa",
        "direction": "debit",
        "amount_minor": 2_000_000,  # 20,000.00 TZS
        "currency": "TZS",
        "counterparty": "DUKA LA MAMA",
        "reference": "QWE123456",
        "balance_after_minor": 5_000_000,
        "occurred_at": BASE_MS,
        "category": "food",
        "confidence": 0.9,
        "parser_version": 3,
        "source": "parsed",
        "is_user_edited": False,
        "created_at": BASE_MS,
        "updated_at": BASE_MS,
        "deleted_at": None,
    }
    tx.update(overrides)
    return tx


async def do_sync(
    client: AsyncClient,
    session: dict[str, str],
    cursor: str = "0",
    changes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    response = await client.post(
        SYNC_URL,
        json={"cursor": cursor, "changes": changes or []},
        headers=auth_headers(session),
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def by_uuid(changes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {c["uuid"]: c for c in changes}


# ── 1. Round trip ─────────────────────────────────────────────────────────


async def test_round_trip_never_returns_own_writes(
    client: AsyncClient, user_a: dict[str, str]
) -> None:
    """Push 3 rows, pull with the returned cursor, get 0 rows back."""
    rows = [make_tx(), make_tx(), make_tx()]
    pushed = await do_sync(client, user_a, "0", rows)
    assert pushed["changes"] == []

    pulled = await do_sync(client, user_a, pushed["cursor"])
    assert pulled["changes"] == []
    assert pulled["cursor"] == pushed["cursor"]


# ── 2. Second device ──────────────────────────────────────────────────────


async def test_second_device_receives_all_rows(
    client: AsyncClient, user_a: dict[str, str]
) -> None:
    """Device A pushes; device B syncs from cursor 0 and receives A's rows.

    Both devices are the same *user* — the second device is modelled as a
    second sync session starting from cursor "0", which is exactly what a fresh
    install does after signing in.
    """
    rows = [make_tx(), make_tx(), make_tx()]
    await do_sync(client, user_a, "0", rows)

    device_b = await do_sync(client, user_a, "0")
    assert len(device_b["changes"]) == 3
    assert by_uuid(device_b["changes"]).keys() == {r["uuid"] for r in rows}


# ── 3. Last write wins ────────────────────────────────────────────────────


@pytest.mark.parametrize("newer_first", [False, True])
async def test_last_write_wins_regardless_of_arrival_order(
    client: AsyncClient, user_a: dict[str, str], newer_first: bool
) -> None:
    tx_uuid = str(uuid_lib.uuid4())
    older = make_tx(uuid=tx_uuid, counterparty="OLD", updated_at=BASE_MS)
    newer = make_tx(uuid=tx_uuid, counterparty="NEW", updated_at=BASE_MS + 1000)

    order = [newer, older] if newer_first else [older, newer]
    for tx in order:
        await do_sync(client, user_a, "0", [tx])

    stored = await do_sync(client, user_a, "0")
    assert len(stored["changes"]) == 1
    assert stored["changes"][0]["counterparty"] == "NEW"
    assert stored["changes"][0]["updated_at"] == BASE_MS + 1000


# ── 4. Stale write ignored ────────────────────────────────────────────────


async def test_stale_write_is_silently_ignored(
    client: AsyncClient, user_a: dict[str, str]
) -> None:
    """An older updated_at is a no-op, not an error. 200, row unchanged."""
    tx_uuid = str(uuid_lib.uuid4())
    current = make_tx(uuid=tx_uuid, counterparty="CURRENT", updated_at=BASE_MS + 5000)
    await do_sync(client, user_a, "0", [current])

    stale = make_tx(uuid=tx_uuid, counterparty="STALE", updated_at=BASE_MS)
    response = await client.post(
        SYNC_URL,
        json={"cursor": "0", "changes": [stale]},
        headers=auth_headers(user_a),
    )
    assert response.status_code == 200

    stored = by_uuid(response.json()["changes"])
    # The losing row is handed back so the client can correct itself.
    assert stored[tx_uuid]["counterparty"] == "CURRENT"
    assert stored[tx_uuid]["updated_at"] == BASE_MS + 5000


async def test_equal_updated_at_is_also_a_noop(
    client: AsyncClient, user_a: dict[str, str]
) -> None:
    """<= loses, not just <. Equal timestamps must not thrash the row."""
    tx_uuid = str(uuid_lib.uuid4())
    await do_sync(client, user_a, "0", [make_tx(uuid=tx_uuid, counterparty="FIRST")])

    result = await do_sync(
        client, user_a, "0", [make_tx(uuid=tx_uuid, counterparty="SECOND")]
    )
    assert by_uuid(result["changes"])[tx_uuid]["counterparty"] == "FIRST"


# ── 5. Tombstone propagates ───────────────────────────────────────────────


async def test_tombstone_propagates_as_a_row_not_an_absence(
    client: AsyncClient, user_a: dict[str, str]
) -> None:
    tx_uuid = str(uuid_lib.uuid4())
    await do_sync(client, user_a, "0", [make_tx(uuid=tx_uuid)])

    deleted_ms = BASE_MS + 60_000
    await do_sync(
        client,
        user_a,
        "0",
        [make_tx(uuid=tx_uuid, deleted_at=deleted_ms, updated_at=deleted_ms)],
    )

    device_b = await do_sync(client, user_a, "0")
    assert len(device_b["changes"]) == 1, "the row must arrive, not vanish"
    assert device_b["changes"][0]["deleted_at"] == deleted_ms


# ── 6. No resurrection ────────────────────────────────────────────────────


async def test_older_non_deleted_copy_cannot_resurrect_a_tombstone(
    client: AsyncClient, user_a: dict[str, str]
) -> None:
    tx_uuid = str(uuid_lib.uuid4())
    deleted_ms = BASE_MS + 60_000

    await do_sync(
        client,
        user_a,
        "0",
        [make_tx(uuid=tx_uuid, deleted_at=deleted_ms, updated_at=deleted_ms)],
    )

    # A device that never learned about the delete replays its old copy.
    await do_sync(
        client, user_a, "0", [make_tx(uuid=tx_uuid, deleted_at=None, updated_at=BASE_MS)]
    )

    stored = await do_sync(client, user_a, "0")
    assert stored["changes"][0]["deleted_at"] == deleted_ms


# ── 7. Idempotent retry ───────────────────────────────────────────────────


async def test_identical_batch_twice_is_a_noop(
    client: AsyncClient, user_a: dict[str, str]
) -> None:
    """The client retries on timeout, possibly after the server committed."""
    rows = [make_tx(), make_tx()]

    first = await do_sync(client, user_a, "0", rows)
    second = await do_sync(client, user_a, "0", rows)

    # Nothing changed, so no new sync_seq was issued and the cursor stands still.
    assert second["cursor"] == first["cursor"]

    everything = await do_sync(client, user_a, "0")
    assert len(everything["changes"]) == 2


# ── 8. Money survives exactly ─────────────────────────────────────────────


async def test_money_is_an_exact_integer(client: AsyncClient, user_a: dict[str, str]) -> None:
    await do_sync(
        client,
        user_a,
        "0",
        [make_tx(amount_minor=2_000_000, balance_after_minor=123_456_789)],
    )

    row = (await do_sync(client, user_a, "0"))["changes"][0]

    assert row["amount_minor"] == 2_000_000
    assert row["balance_after_minor"] == 123_456_789
    # Not a float, not a Decimal string. A float here would mean something did
    # money arithmetic in binary floating point.
    assert isinstance(row["amount_minor"], int)
    assert not isinstance(row["amount_minor"], bool)
    assert isinstance(row["balance_after_minor"], int)


async def test_float_amount_is_rejected(client: AsyncClient, user_a: dict[str, str]) -> None:
    """A float amount is a 422, never a silent truncation."""
    response = await client.post(
        SYNC_URL,
        json={"cursor": "0", "changes": [make_tx(amount_minor=20000.5)]},
        headers=auth_headers(user_a),
    )
    assert response.status_code == 422


async def test_large_amount_survives_beyond_32_bits(
    client: AsyncClient, user_a: dict[str, str]
) -> None:
    """BIGINT, not INTEGER: 21.5 billion minor units overflows int32."""
    huge = 21_474_836_470
    await do_sync(client, user_a, "0", [make_tx(amount_minor=huge)])
    assert (await do_sync(client, user_a, "0"))["changes"][0]["amount_minor"] == huge


# ── 9. Millisecond fidelity ───────────────────────────────────────────────


async def test_millisecond_fidelity(client: AsyncClient, user_a: dict[str, str]) -> None:
    occurred = 1_786_000_000_123
    await do_sync(
        client,
        user_a,
        "0",
        [make_tx(occurred_at=occurred, created_at=occurred, updated_at=occurred)],
    )

    row = (await do_sync(client, user_a, "0"))["changes"][0]
    assert row["occurred_at"] == occurred
    assert row["created_at"] == occurred
    assert row["updated_at"] == occurred


async def test_seconds_timestamp_is_rejected(
    client: AsyncClient, user_a: dict[str, str]
) -> None:
    """A seconds value where ms was expected is an off-by-1000, caught here."""
    response = await client.post(
        SYNC_URL,
        json={"cursor": "0", "changes": [make_tx(occurred_at=1_786_000_000)]},
        headers=auth_headers(user_a),
    )
    assert response.status_code == 422


# ── 10. Tenant isolation ──────────────────────────────────────────────────


async def test_user_b_never_sees_user_a_rows(
    client: AsyncClient, user_a: dict[str, str], user_b: dict[str, str]
) -> None:
    a_rows = [make_tx(), make_tx()]
    await do_sync(client, user_a, "0", a_rows)

    a_uuids = {r["uuid"] for r in a_rows}

    # Every cursor value, not just 0 — a low cursor is the case that would leak.
    for cursor in ("0", "1", "2", "999999"):
        pulled = await do_sync(client, user_b, cursor)
        assert not (by_uuid(pulled["changes"]).keys() & a_uuids)


async def test_same_uuid_for_two_users_stays_separate(
    client: AsyncClient, user_a: dict[str, str], user_b: dict[str, str]
) -> None:
    """uuid is unique per user, not globally."""
    shared = str(uuid_lib.uuid4())
    await do_sync(client, user_a, "0", [make_tx(uuid=shared, counterparty="ALICE")])
    await do_sync(client, user_b, "0", [make_tx(uuid=shared, counterparty="BOB")])

    assert (await do_sync(client, user_a, "0"))["changes"][0]["counterparty"] == "ALICE"
    assert (await do_sync(client, user_b, "0"))["changes"][0]["counterparty"] == "BOB"


# ── Beyond the ten ────────────────────────────────────────────────────────


async def test_paging_drains_completely(client: AsyncClient, user_a: dict[str, str]) -> None:
    """SYNC_MAX_PULL is 5 in tests; 12 rows must arrive across pages."""
    rows = [make_tx() for _ in range(12)]
    await do_sync(client, user_a, "0", rows)

    seen: set[str] = set()
    cursor = "0"
    for _ in range(10):  # generous bound; the loop must exit via has_more
        page = await do_sync(client, user_a, cursor)
        seen.update(c["uuid"] for c in page["changes"])
        cursor = page["cursor"]
        if not page["has_more"]:
            break
    else:
        pytest.fail("paging did not terminate")

    assert seen == {r["uuid"] for r in rows}


async def test_duplicate_uuids_in_one_batch(
    client: AsyncClient, user_a: dict[str, str]
) -> None:
    """Postgres refuses to update the same row twice in one statement.

    The client can queue two edits to one row between syncs, so the batch is
    collapsed to the newest before it reaches the upsert.
    """
    tx_uuid = str(uuid_lib.uuid4())
    batch = [
        make_tx(uuid=tx_uuid, counterparty="FIRST", updated_at=BASE_MS),
        make_tx(uuid=tx_uuid, counterparty="SECOND", updated_at=BASE_MS + 1),
    ]
    result = await do_sync(client, user_a, "0", batch)
    assert result["changes"] == []

    stored = await do_sync(client, user_a, "0")
    assert len(stored["changes"]) == 1
    assert stored["changes"][0]["counterparty"] == "SECOND"


async def test_oversized_batch_is_rejected(
    client: AsyncClient, user_a: dict[str, str]
) -> None:
    response = await client.post(
        SYNC_URL,
        json={"cursor": "0", "changes": [make_tx() for _ in range(1001)]},
        headers=auth_headers(user_a),
    )
    assert response.status_code in (413, 422)


async def test_sync_requires_authentication(client: AsyncClient) -> None:
    response = await client.post(SYNC_URL, json={"cursor": "0", "changes": []})
    assert response.status_code == 401


async def test_raw_text_fields_are_rejected(
    client: AsyncClient, user_a: dict[str, str]
) -> None:
    """There is no endpoint that accepts raw message text, by design.

    `extra="forbid"` means a client bug that tried to attach the notification
    body fails loudly rather than having it silently dropped.
    """
    for field in ("body", "title", "raw_text", "capture_id"):
        response = await client.post(
            SYNC_URL,
            json={"cursor": "0", "changes": [make_tx(**{field: "M-PESA. Umelipa..."})]},
            headers=auth_headers(user_a),
        )
        assert response.status_code == 422, f"{field} was accepted"


async def test_unknown_provider_and_category_are_tolerated(
    client: AsyncClient, user_a: dict[str, str]
) -> None:
    """The client renders an unrecognised provider rather than erroring, so the
    server must not be stricter than the client that produced the row."""
    await do_sync(
        client, user_a, "0", [make_tx(provider="newbank", category="pets")]
    )
    row = (await do_sync(client, user_a, "0"))["changes"][0]
    assert row["provider"] == "newbank"
    assert row["category"] == "pets"


async def test_null_optional_fields_round_trip(
    client: AsyncClient, user_a: dict[str, str]
) -> None:
    """counterparty, reference, balance and category are sparse in practice."""
    await do_sync(
        client,
        user_a,
        "0",
        [
            make_tx(
                counterparty=None,
                reference=None,
                balance_after_minor=None,
                category=None,
            )
        ],
    )
    row = (await do_sync(client, user_a, "0"))["changes"][0]
    assert row["counterparty"] is None
    assert row["reference"] is None
    assert row["balance_after_minor"] is None
    assert row["category"] is None


async def test_is_user_edited_accepts_client_integers(
    client: AsyncClient, user_a: dict[str, str]
) -> None:
    """The client column is INTEGER 0/1, so 1 must be accepted as true."""
    await do_sync(client, user_a, "0", [make_tx(is_user_edited=1)])
    assert (await do_sync(client, user_a, "0"))["changes"][0]["is_user_edited"] is True


async def test_user_edited_row_is_not_special_cased(
    client: AsyncClient, user_a: dict[str, str]
) -> None:
    """Conflict resolution orders on updated_at alone.

    A hand-corrected row wins because the edit bumped its timestamp, not because
    of a flag. A newer machine-parsed row must therefore still beat an older
    user-edited one at this layer — the client is what refuses to re-parse.
    """
    tx_uuid = str(uuid_lib.uuid4())
    await do_sync(
        client,
        user_a,
        "0",
        [make_tx(uuid=tx_uuid, is_user_edited=True, counterparty="EDITED", updated_at=BASE_MS)],
    )
    await do_sync(
        client,
        user_a,
        "0",
        [
            make_tx(
                uuid=tx_uuid,
                is_user_edited=False,
                counterparty="PARSED",
                updated_at=BASE_MS + 1000,
            )
        ],
    )
    assert (await do_sync(client, user_a, "0"))["changes"][0]["counterparty"] == "PARSED"


async def test_invalid_cursor_is_rejected(
    client: AsyncClient, user_a: dict[str, str]
) -> None:
    for bad in ("abc", "-1"):
        response = await client.post(
            SYNC_URL,
            json={"cursor": bad, "changes": []},
            headers=auth_headers(user_a),
        )
        assert response.status_code == 422, bad


async def test_cursor_omitted_defaults_to_zero(
    client: AsyncClient, user_a: dict[str, str]
) -> None:
    await do_sync(client, user_a, "0", [make_tx()])
    response = await client.post(SYNC_URL, json={}, headers=auth_headers(user_a))
    assert response.status_code == 200
    assert len(response.json()["changes"]) == 1


async def test_server_time_is_epoch_milliseconds(
    client: AsyncClient, user_a: dict[str, str]
) -> None:
    body = await do_sync(client, user_a, "0")
    # 13 digits is the ms range for any date between 2001 and 2286; a seconds
    # value would be 10.
    assert 1_600_000_000_000 < body["server_time"] < 4_102_444_800_000


async def test_deleted_user_rows_do_not_leak_to_a_new_account(
    client: AsyncClient, user_a: dict[str, str]
) -> None:
    """A fresh account starts empty even at cursor 0 with rows in the table."""
    await do_sync(client, user_a, "0", [make_tx(), make_tx()])
    fresh = await register_user(client, "carol@example.com", "Carol")
    assert (await do_sync(client, fresh, "0"))["changes"] == []

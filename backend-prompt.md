# Build the sync backend for PersonalFinanceAssistant

You are building the server for an existing, working Android app. **The client is
already built and is the source of truth for the data model** — your job is to
receive what it already produces, not to redesign it. Read the "Client contract"
section as a specification, not a suggestion.

## Stack

- **FastAPI**, Python 3.12+
- **PostgreSQL 16** with **async SQLAlchemy 2.0** (`asyncpg`)
- **Alembic** for migrations — every schema change is a migration, no
  `create_all()` anywhere
- **Pydantic v2** for request/response schemas
- **Docker Compose** for local dev (api + postgres)
- **pytest** + `httpx.AsyncClient` for tests, against a real Postgres (a
  throwaway container or a test schema), not SQLite — the sync upsert relies on
  `ON CONFLICT`, which SQLite would not exercise faithfully

---

## What the app is

An Android app that reads the user's **notifications**, extracts bank and mobile
money transactions from them (Tanzanian providers: M-Pesa, Airtel Money, Mixx by
Yas, HaloPesa, CRDB, NMB, NBC), and shows spending analysis. All parsing happens
on the device. Currency is always `TZS` today.

The app currently works fully offline with a local SQLite database. It has a
mock auth adapter behind a single interface, so swapping in your API should
touch one client file.

---

## The one thing you must not build

**Raw message text never leaves the device, and there is no endpoint that
accepts it.**

The client stores captured notification text in a `captures` table which is
deliberately walled off — a lint rule stops any UI file from even importing the
module that can read it. `captures` has no UUID, no `updated_at` and no
tombstone precisely because it was designed never to sync.

Only **transactions** sync. If you find yourself adding a `body`, `title` or
`raw_text` column, stop: that is a privacy regression, not a feature.

---

## Client contract

### The row the client will send you

This is the client's `transactions` table after its v3 migration. Types are as
stored on device.

| Field | Type | Notes |
|---|---|---|
| `uuid` | string | **Client-generated UUID v4. The identity. Never reassign it.** |
| `provider` | string | One of: `mpesa`, `airtel`, `tigo`, `halo`, `crdb`, `nmb`, `nbc` |
| `direction` | string | `credit` or `debit` |
| `amount_minor` | integer | **Integer minor units (cents). Never a float.** 20,000.00 TZS is `2000000` |
| `currency` | string | Always `TZS` today; keep the column, do not hardcode |
| `counterparty` | string \| null | Free text, often ALL CAPS, frequently null |
| `reference` | string \| null | Provider's own transaction reference when the message stated one |
| `balance_after_minor` | integer \| null | Sparse — only when the message stated a balance |
| `occurred_at` | integer | **Epoch milliseconds** |
| `category` | string \| null | One of: `food`, `transport`, `bills`, `rent`, `health`, `shopping`, `transfer`, `income`, `other` |
| `confidence` | float | 0.0–1.0, the parser's self-assessment |
| `source` | string | `parsed` or `manual` |
| `is_user_edited` | boolean | The user corrected this row by hand |
| `created_at` | integer | Epoch milliseconds |
| `updated_at` | integer | Epoch milliseconds |
| `deleted_at` | integer \| null | **Tombstone.** Non-null means deleted |

The client's local `id` and `capture_id` are device-local rowids. **They are not
sent and must not exist server-side.**

### Rules that will cause real bugs if you miss them

1. **Money is integer minor units.** Never convert to float, never use
   `NUMERIC` arithmetic that could round, never divide to "get the amount". Use
   `BIGINT`. A trailing full stop in a message already inflated an amount 100×
   once in this codebase; treat money arithmetic as adversarial.

2. **`is_user_edited = true` means the machine must not overwrite it.** The
   client already refuses to re-parse those rows. If you ever add server-side
   enrichment (categorisation, merchant cleanup), it must skip them too.

3. **Deletes are tombstones, never `DELETE`.** A hard delete cannot propagate —
   the row would simply return from the other side on the next sync.

4. **`occurred_at` is when the notification arrived**, not a date parsed from
   the message text. It can be wrong for delayed notifications. Do not build
   anything that assumes it is the true transaction time.

5. **Timestamps are epoch milliseconds on the wire.** Store as `TIMESTAMPTZ`
   (microsecond precision, so milliseconds round-trip exactly) and convert in
   one place at the API boundary. Do not let millisecond values reach the DB as
   seconds — an off-by-1000 here is silent and ruins every chart in the app.

---

## Sync protocol

Delta sync with a cursor. One round trip does both directions.

```
POST /v1/sync
Authorization: Bearer <access token>

{
  "cursor": "0",                    // opaque; "0" or omitted on first sync
  "changes": [ <transaction>, ... ] // rows changed locally since last sync
}

200 OK
{
  "cursor": "4821",                 // pass back next time
  "changes": [ <transaction>, ... ], // rows the server has that the client lacks
  "server_time": 1786000000000
}
```

### The cursor must be a server-assigned sequence, not a timestamp

Give every transaction row a `sync_seq BIGINT` populated from a Postgres
sequence, bumped on **every** insert and update. The cursor is the highest
`sync_seq` the client has seen.

Do not use `updated_at` as the cursor. Client clocks are wrong, two rows can
share a millisecond, and a row written during a long transaction can be
committed after a later one — all of which silently drop rows from a
timestamp-based pull. A sequence has none of those failure modes.

### Conflict resolution: last write wins on `updated_at`

- Incoming row has no server counterpart → insert.
- Incoming `updated_at` > stored `updated_at` → update.
- Incoming `updated_at` <= stored `updated_at` → **ignore, and do not error.**
  A no-op is a normal outcome, not a failure.

A user edit always bumps `updated_at` on the client, so a hand-corrected row
naturally wins over a stale machine-parsed one. **Do not add a special case for
`is_user_edited` in conflict resolution** — it would only reorder writes the
timestamps already order correctly.

Tombstones follow the same rule: a delete is just an update that sets
`deleted_at`. Never resurrect a row because the incoming copy has
`deleted_at: null` and an older `updated_at`.

### Idempotency

`POST /v1/sync` must be safe to retry. The client sends over a mobile network
and will retry on timeout, quite possibly after the server already committed.
Upsert on `(user_id, uuid)`; a replayed batch must produce no change and no
error.

---

## Auth

Email + password, JWT access + refresh. This matches the client's existing
screens, so only its API adapter changes.

```
POST /v1/auth/register  { email, password, display_name } -> { access, refresh, user }
POST /v1/auth/login     { email, password }               -> { access, refresh, user }
POST /v1/auth/refresh   { refresh }                       -> { access, refresh }
POST /v1/devices        { device_id }                     -> 204
```

- Hash with **Argon2id** (or bcrypt). Never store or log a password.
- Access token short-lived (~15 min); refresh token long-lived and **rotating** —
  each use issues a new one and invalidates the old.
- `user` is `{ id, email, display_name }` — that shape is what the client
  already expects.
- Every sync query is scoped by `user_id`. There must be no code path that can
  return another user's transactions; make `user_id` part of the primary lookup,
  not an afterthought filter.

**Client-side note for whoever wires this up:** the app's `ApiAdapter` interface
(`src/services/api.ts`) currently has `login`, `register` and `registerDevice`
but **no refresh call**. Adding refresh means extending that interface — a small,
contained change, but it is not free.

---

## Data model (server)

```
users            id, email (unique, citext), password_hash, display_name,
                 created_at, updated_at
refresh_tokens   id, user_id, token_hash, expires_at, revoked_at
devices          id, user_id, device_id (unique per user), last_seen_at
transactions     id, user_id, uuid, sync_seq, <all client fields above>
                 UNIQUE (user_id, uuid)
                 INDEX (user_id, sync_seq)
                 INDEX (user_id, occurred_at DESC)
```

`uuid` is unique **per user**, not globally — two users could theoretically
generate the same UUID, and scoping the constraint costs nothing.

---

## Milestones

1. **Skeleton** — FastAPI app, Docker Compose with Postgres, Alembic wired,
   health endpoint, settings from environment. No business logic.
2. **Auth** — users, register/login/refresh, password hashing, JWT middleware,
   `user_id` dependency. Tests for the whole flow including token rotation.
3. **Transactions + sync** — model, migration, `POST /v1/sync` with upsert,
   cursor, conflict resolution, idempotency. This is the substance.
4. **Hardening** — rate limits on auth, request size cap on sync batches,
   structured logging that never logs a token or a counterparty, CI running
   migrations and tests.

---

## Acceptance criteria

Write tests that prove each of these. They are the behaviours that actually
matter; a passing CRUD suite that skips them proves nothing.

1. **Round trip.** Push 3 rows, pull with the returned cursor, get 0 rows back
   (a client must never be handed its own writes again).
2. **Second device.** Device A pushes; device B syncs from cursor `0` and
   receives all of A's rows.
3. **Last write wins.** Push the same `uuid` twice with different
   `updated_at`; the newer content survives regardless of arrival order.
4. **Stale write ignored.** Push an older `updated_at` for an existing row; it
   is silently ignored, response is 200, stored row unchanged.
5. **Tombstone propagates.** Device A deletes; device B pulls and receives the
   row with `deleted_at` set — not an absence.
6. **No resurrection.** After a delete, replaying an older non-deleted copy of
   that row leaves it deleted.
7. **Idempotent retry.** Post the identical batch twice; the second changes
   nothing and returns 200.
8. **Money survives exactly.** Push `amount_minor: 2000000`, read it back as
   `2000000`. Assert the type is an integer, not a float or Decimal string.
9. **Millisecond fidelity.** Push `occurred_at: 1786000000123`, read back
   exactly `1786000000123`.
10. **Tenant isolation.** User A's rows are never returned to user B, at any
    cursor value.

---

## Explicit non-goals

- **No parsing.** The device parses; the server stores. Do not add regex rules
  or a categoriser — they would drift from the client's and produce two
  different answers for one message.
- **No cross-device deduplication.** The client already collapses the case where
  one payment arrives as both an app push and an SMS. Two *different phones*
  capturing the same payment would produce two UUIDs and two rows; that is a
  known, accepted limitation, not something to solve speculatively.
- **No raw message text**, as above.
- **No push notifications, no background jobs, no analytics pipeline** in this
  build.

---

## Working style

- Every schema change is an Alembic migration, and every migration must be
  tested against a database that already contains rows — not just a fresh one.
- Type-annotate fully; run `mypy` and `ruff` in CI.
- When something in this document turns out to be wrong or under-specified, say
  so and propose the fix rather than guessing silently. The client's constraints
  are real and were learned the hard way; if one of them seems to make an
  endpoint impossible, that is worth raising, not routing around.

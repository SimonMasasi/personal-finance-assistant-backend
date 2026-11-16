# PersonalFinanceAssistant — sync backend

Sync server for the PersonalFinanceAssistant Android app. The app reads the user's
notifications, extracts bank and mobile-money transactions from them on device, and works fully
offline against a local SQLite database. This server exists so those transactions follow the
user across devices — and to do nothing else.

**The client is the source of truth for the data model.** This server receives what the app
already produces; it does not redesign it.

---

## Table of contents

- [The one thing this server must not do](#the-one-thing-this-server-must-not-do)
- [Tech stack](#tech-stack)
- [Prerequisites](#prerequisites)
- [Project structure](#project-structure)
- [Environment variables](#environment-variables)
- [Installation](#installation)
- [Running](#running)
- [Database migrations](#database-migrations)
- [Running tests](#running-tests)
- [API reference](#api-reference)
- [How the sync protocol works](#how-the-sync-protocol-works)
- [Client-side changes still required](#client-side-changes-still-required)
- [Known gaps](#known-gaps)

---

## The one thing this server must not do

**Raw message text never leaves the device, and there is no endpoint that accepts it.**

The client stores captured notification text in a `captures` table that is deliberately walled
off — a lint rule stops any UI file from importing the module that can read it, and `captures`
has no UUID, no `updated_at` and no tombstone precisely because it was designed never to sync.

Only **transactions** sync. If you find yourself adding a `body`, `title` or `raw_text` column,
stop: that is a privacy regression, not a feature. Two tests enforce this structurally rather
than by convention — `TestPrivacyInvariant` in `src/tests/test_hardening.py` fails if any
request schema in the OpenAPI document or any column on the `transactions` model gains a
field that could carry message text.

`counterparty` is treated as personal data for the same reason: it is free text lifted verbatim
from a bank SMS and it names real people. The log filter redacts it.

---

## Tech stack

| Layer | Technology |
|---|---|
| Framework | FastAPI (Python 3.12+) |
| Database | PostgreSQL 16 |
| ORM | async SQLAlchemy 2.0 with `asyncpg` |
| Migrations | Alembic — every schema change, no `create_all()` anywhere |
| Schemas | Pydantic v2 |
| Identity | Google Sign-In. No passwords are stored, hashed or reset — the column is gone. |
| Tokens | PyJWT access tokens; opaque, hashed, rotating refresh tokens |
| Rate limiting | slowapi |
| Package manager | `uv` |
| Tests | pytest + `httpx.AsyncClient`, against a **real Postgres** |
| Docs | Scalar at `/scalar` |

### Where this differs from `meeting-assistant-backend`

The directory layout, router registry, module-level singletons and `uv` tooling are copied from
that project deliberately. Four of its conventions are not, and each has a reason:

- **No `SingleResponse`/`ListResponse` envelope and no `assets/responses.json`.** The sync body
  is pinned by the client contract (`{cursor, changes, server_time}`), and the client's SQLite
  column names are snake_case. Wrapping would add a rename layer over every money field.
- **No `to_camel` alias generator.** `amount_minor` stays `amount_minor`, matching the column it
  came from.
- **Real HTTP status codes.** meeting-assistant returns 200 with `status: false`. A phone on a
  mobile network needs to distinguish "token expired, refresh and retry" (401) from "batch too
  big, split it" (413) from "server broke, back off" (500).
- **Migrations do not run at import time in `src/main.py`.** Running them implicitly on boot makes it
  easy to never notice one is missing. Compose and CI run `alembic upgrade head` explicitly.

---

## Prerequisites

- **Python 3.12+**
- **PostgreSQL 16** — or just Docker, which the compose file uses
- **[uv](https://docs.astral.sh/uv/)** — `curl -LsSf https://astral.sh/uv/install.sh | sh`

---

## Project structure

```
personal-finance-assistant-backend/
├── conftest.py                # ROOT conftest: pins env vars before src/core/config.py imports
├── alembic/versions/          # 0001 users+auth, 0002 transactions+sync_seq, 0003 google auth
├── docker/
│   ├── docker-compose.yml     # api + postgres (run from the repo root with -f)
│   └── docker-compose.prod.yml  # production overlay
└── src/
    ├── main.py                # FastAPI app: middleware, router loop, uvicorn entry
    ├── core/
    │   ├── config.py          # pydantic-settings Settings + SETTINGS singleton
    │   └── logging.py         # JSON logging + hard redaction
    ├── modules/
    │   ├── auth/              # dtos, models, services, views, plus:
    │   │   ├── dependencies.py      # get_current_user_id / CurrentUserId
    │   │   ├── tokens.py            # our own JWTs (JWTAuth)
    │   │   ├── google.py            # Google ID token verification
    │   │   └── mint_test_session.py # dev tool: python -m src.modules.auth.mint_test_session
    │   ├── sync/              # dtos, models, services, views  ← the substance
    │   └── health/            # views
    ├── shared/
    │   ├── base_model.py      # DeclarativeBase + TimestampMixin
    │   ├── database.py        # async engine, session factory, get_session
    │   ├── errors.py          # AppError hierarchy + global handlers
    │   ├── middleware.py      # body size cap
    │   ├── rate_limit.py      # slowapi limiter
    │   ├── routes.py          # the router registry
    │   └── time.py            # epoch-ms <-> datetime, THE single conversion point
    ├── utils/                 # open_api_routes, startup_banner
    └── tests/
```

Adding a module is one import and one entry in `src/shared/routes.py`.

---

## Environment variables

```dotenv
# ── Required ──────────────────────────────────────────────────────────────
# Must be the asyncpg URL — SQLAlchemy picks the driver from the scheme, and a
# plain postgresql:// fails at engine creation.
DATABASE_URL="postgresql+asyncpg://pfa:pfa@localhost:5433/pfa"
SECRET_KEY="at-least-32-random-characters"

# ── App ───────────────────────────────────────────────────────────────────
DEBUG=True
APP_HOST="0.0.0.0"
APP_PORT=8000

# ── Tokens ────────────────────────────────────────────────────────────────
JWT_ACCESS_TOKEN_EXPIRE_SECONDS=900       # ~15 min; no revocation list, so keep it short
REFRESH_TOKEN_EXPIRE_SECONDS=2592000      # 30 days; safe only because they rotate

# ── Sync limits ───────────────────────────────────────────────────────────
SYNC_MAX_BATCH=1000       # a phone's backlog; past this the client should page
SYNC_MAX_PULL=500         # rows per pull; the client drains the rest via has_more
MAX_REQUEST_BYTES=2097152 # enforced before JSON parsing

# ── CORS ──────────────────────────────────────────────────────────────────
CORS_ALLOW_ORIGINS="*"    # the Android client sends no Origin; this is for browser tooling
```

> **Port 5433, not 5432.** The compose file publishes Postgres on 5433 so it does not clash with
> a natively installed Postgres, which wins the bind for `localhost` and silently shadows the
> container.

---

## Installation

```bash
cp .env.example .env          # then edit SECRET_KEY
uv sync
docker compose -f docker/docker-compose.yml up -d db
uv run alembic upgrade head
```

---

## Running

```bash
uv run python -m src.main     # http://localhost:8000, docs at /scalar
```

Or the whole stack, with migrations applied on boot:

```bash
docker compose -f docker/docker-compose.yml up --build
```

---

## Database migrations

```bash
uv run alembic upgrade head                       # apply everything
uv run alembic revision --autogenerate -m "..."   # generate from model changes
uv run alembic downgrade -1                       # step back one
uv run alembic current                            # what is applied
uv run alembic history --verbose                  # the full chain
```

Every schema change is a migration — there is no `create_all()` in this codebase, including in
the tests. `src/tests/test_migrations.py` enforces two things worth knowing about:

- **Migrations are tested against a populated database.** It stops at `0001`, inserts a real
  user, then applies `0002` on top and checks the row survived. A migration that only ever runs
  against an empty database proves nothing about the upgrade every deployment after the first
  performs.
- **Models and migrations may not drift.** An Alembic autogenerate comparison must produce no
  structural diff, which catches the most common real mistake: adding a column to a model and
  forgetting the migration.

---

## Running tests

```bash
docker compose -f docker/docker-compose.yml up -d db   # tests need a real Postgres
uv run pytest -q
uv run pytest src/tests/test_sync.py -q          # the acceptance criteria
uv run pytest src/tests/test_time.py src/tests/test_jwt_auth.py src/tests/test_google_auth.py src/tests/test_logging.py -q  # pure logic
uv run pytest -k "tombstone or resurrect" -v
uv run ruff check . && uv run mypy .
```

Tests run against a real Postgres (`pfa_test`, created automatically), **not** SQLite. The sync
upsert relies on `ON CONFLICT` and `users.email` is `CITEXT`; SQLite would exercise neither
faithfully, so a green suite against it would prove nothing about the behaviour that matters.

`src/tests/test_sync.py` covers the ten acceptance criteria from `backend-prompt.md`, one test
each: round trip, second device, last write wins (both arrival orders), stale write ignored,
tombstone propagates, no resurrection, idempotent retry, money exact, millisecond fidelity, and
tenant isolation.

---

## API reference

All endpoints are `/v1`. Bodies are bare snake_case JSON — no envelope.

| Method | Path | Auth | Body | Returns |
|---|---|---|---|---|
| `GET` | `/health` | — | — | `{status}` |
| `GET` | `/health/ready` | — | — | `{status, database}` |
| `POST` | `/v1/auth/google` | — | `{id_token}` | `{access, refresh, user}` |
| `POST` | `/v1/auth/refresh` | — | `{refresh}` | `{access, refresh}` |
| `POST` | `/v1/devices` | Bearer | `{device_id}` | 204 |
| `POST` | `/v1/sync` | Bearer | `{cursor, changes[]}` | `{cursor, changes[], server_time, has_more}` |

`user` is `{id, email, display_name}`.

Errors are `{"error": {"code", "message"}}` with a real status code: 401 `unauthorized`,
409 `conflict`, 413 `payload_too_large`, 422 `invalid_input`, 500 `internal_error`.

---

## How the sync protocol works

One round trip does both directions.

```
POST /v1/sync
Authorization: Bearer <access token>

{ "cursor": "0", "changes": [ <transaction>, ... ] }

200 OK
{ "cursor": "4821", "changes": [ ... ], "server_time": 1786000000000, "has_more": false }
```

### The cursor is a sequence, not a timestamp

Every transaction row carries `sync_seq BIGINT`, taken from a Postgres sequence and bumped on
**every** insert and update. The cursor is the highest `sync_seq` the client has seen.

`updated_at` would not work: client clocks are wrong, two rows can share a millisecond, and a row
written inside a long transaction can commit after one with a later timestamp — each of which
silently drops rows from a timestamp-based pull. A sequence has none of those failure modes.

### Conflict resolution: last write wins on `updated_at`

The entire policy is one SQL clause:

```sql
INSERT INTO transactions (...) VALUES (...)
ON CONFLICT ON CONSTRAINT uq_transactions_user_uuid DO UPDATE
  SET ..., sync_seq = nextval('transaction_sync_seq')
  WHERE transactions.updated_at < EXCLUDED.updated_at
RETURNING uuid
```

- No server counterpart → insert.
- Incoming `updated_at` newer → update.
- Incoming `updated_at` older *or equal* → the `WHERE` fails, the row is untouched, **no error**.

That last line delivers four behaviours at once: a replayed batch is a no-op (idempotent retry),
a stale write is silently ignored rather than a 409, a tombstone cannot be resurrected by an
older non-deleted copy (a delete is just an update that set `deleted_at`, so it wins on the same
comparison), and concurrent devices converge regardless of arrival order.

There is deliberately **no special case for `is_user_edited`**. A user edit always bumps
`updated_at` on the client, so a hand-corrected row already beats a stale machine-parsed one; a
special case would only reorder writes the timestamps already order correctly.

### Why the response excludes only *applied* rows

After the push, the pull excludes the uuids the upsert actually reported through `RETURNING` —
not everything the client sent. A row whose push lost the comparison is deliberately left in, so
the client receives the winning version and corrects itself. Excluding the whole submitted batch
would leave the two sides permanently disagreeing.

### Money and time

- `amount_minor` and `balance_after_minor` are `BIGINT` **integer minor units**. Never `NUMERIC`,
  never float, never divided. A float in a request is a 422, not a silent truncation — a trailing
  full stop in a message once inflated an amount 100× in this codebase.
- Timestamps are **epoch milliseconds** on the wire and `TIMESTAMPTZ` in the database
  (microsecond precision, so milliseconds round-trip exactly). The conversion lives in exactly
  one place, `src/shared/time.py`, because an off-by-1000 here is silent and ruins every chart in
  the app. That module also rejects a *seconds* value outright: seconds timestamps for modern
  dates land in January 1970 when read as milliseconds, so the lower bound is 2000-01-01.

---

## Client-side changes still required

`backend-prompt.md` states the client expects `user` as `{id, email, display_name}`. **It does
not.** The client currently declares:

```ts
export type AuthUser = { id: string; email: string; displayName: string };
export type AuthResponse = { access: string; refresh: string; user: AuthUser };

export interface ApiAdapter {
  googleSignIn(req: { idToken: string }): Promise<AuthResponse>;
  refresh(refreshToken: string): Promise<{ access: string; refresh: string }>;
  registerDevice(deviceId: string, accessToken: string): Promise<void>;
  sync(req: SyncRequest, accessToken: string): Promise<SyncResponse>;
}
```

Wiring this server up therefore needs, in `PersonalFinanceAssistant`:

1. **`src/services/api.ts`** — replace `MockAdapter` with an `HttpAdapter`. The header comment
   already anticipates this: *"When the real server lands, implement HttpAdapter and swap the
   export."*
2. **`AuthResponse`** — `{ token, user }` becomes `{ access, refresh, user }`.
3. **`AuthUser`** — `displayName` becomes `display_name`, or map it at the adapter boundary.
4. **`ApiAdapter`** — add `refresh(token)` and `sync(cursor, changes)`. The prompt flags the
   missing refresh call itself; it is a small but not free change.
5. **`src/services/auth.ts`** — `StoredSession` and `persistSession` must hold two tokens, and
   something needs to catch a 401 and retry after refreshing. There is currently no
   `Authorization` header code anywhere in the app.
6. **A base URL** — there is none today: no `fetch`, no `axios`, no `EXPO_PUBLIC_*`, nothing in
   `app.json`.
7. **A cursor** — persist it in the existing `settings` table via `getSetting`/`setSetting`.

---

## Known gaps

Things worth knowing before this goes anywhere near production.

1. **`ingest.ts` hard-deletes rows — this is the most likely source of a real bug.** Both
   `parseAndStore` paths run `DELETE FROM transactions WHERE capture_id = ?` when a capture stops
   matching after a reparse, or is found to be a cross-notification duplicate. That is a hard
   delete with no `deleted_at`, so a row that had already synced would be pushed back by the
   server on the next pull. The fix belongs on the client (soft-delete there too); the server
   cannot detect it.
2. **Guest mode is unspecified.** `src/store/session.ts` has a `'guest'` status, so the app can
   accumulate transactions with no user at all. The server side is trivial — they arrive as a
   normal first push — but the client must decide whether to adopt or discard them at sign-in.
3. **Paged syncs return the client's own rows once.** When a pull hits `SYNC_MAX_PULL`, the
   cursor only advances as far as the client was served, which sits below the sequence values
   just assigned to its own pushed rows. Those come back on a later round trip. They are
   identical to what the client already has and its own last-write-wins makes them no-ops, so
   this costs bandwidth, not correctness.
4. **Rate limiting is in-memory.** Fine for one instance; more than one replica means pointing
   slowapi at Redis via `storage_uri`, or each replica enforces the limit independently.
5. **A deleted user's access token stays valid for up to 15 minutes.** `get_current_user_id`
   returns the id from the token without loading the user, to avoid a database round trip per
   request. `ON DELETE CASCADE` means such a token can read nothing.
6. **No cross-device deduplication**, by design. The client already collapses one payment
   arriving as both an app push and an SMS. Two *different phones* capturing the same payment
   produce two UUIDs and two rows — a known, accepted limitation.
7. **No parsing, no categorisation, no background jobs, no push notifications.** The device
   parses; the server stores. Server-side rules would drift from the client's and produce two
   different answers for one message.

## Google Sign-In

There is one entry point, `POST /v1/auth/google`. The device obtains a Google ID
token and exchanges it here; the server verifies it and issues its own access +
refresh pair. Everything downstream — rotation, revocation, `/v1/sync` — is
unchanged and knows nothing about how the user proved who they were.

`GOOGLE_CLIENT_IDS` must be the **Web** OAuth client ID, not the Android one.
The Android client (package name + SHA-1) authorises the sign-in on the device
but never appears in the token: the app passes the web client ID as
`webClientId`, and Google puts that in `aud`. Getting this backwards fails every
sign-in with a deliberately generic 401 that reads like a signature problem.

The response is **200 whether or not the account already existed**. A
201-on-create would tell any caller holding a valid token whether that email was
already registered.

### A session for the client's integration test

Sign-in needs a real Google token, which an unattended test cannot obtain. Rather
than add a debug bypass to the endpoint, mint one directly:

```bash
eval "$(uv run python -m src.modules.auth.mint_test_session --email live@example.com)"
```

It calls the same two functions `/v1/auth/google` calls, so its tokens are
indistinguishable from real ones, and it refuses to run against a non-local
database.

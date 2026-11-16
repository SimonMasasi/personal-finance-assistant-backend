from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt

from src.core.config import SETTINGS

# The values the client actually produces. Both lists are validated but NOT
# enforced: `providerLabel()` on the client falls back to rendering the raw id
# for an unrecognised provider, so the server must not be stricter than the
# client that produced the row and reject a sync outright. Unknown values are
# stored as sent.
KNOWN_PROVIDERS = frozenset({"mpesa", "airtel", "tigo", "halo", "crdb", "nmb", "nbc"})
KNOWN_CATEGORIES = frozenset(
    {"food", "transport", "bills", "rent", "health", "shopping", "transfer", "income", "other"}
)

# `confidence` is a float, but 0/1 arrive as ints from JSON. StrictFloat alone
# would reject those, so allow int and coerce.
Confidence = Annotated[StrictFloat | StrictInt, Field(ge=0.0, le=1.0)]


class TransactionDTO(BaseModel):
    """One transaction, in exactly the shape the client's SQLite row has.

    Field names match the client's column names one for one — no alias
    generator, no camelCase — so there is no rename layer to get wrong.
    """

    model_config = ConfigDict(extra="forbid")

    uuid: str = Field(min_length=1, max_length=36)
    provider: str = Field(min_length=1, max_length=32)
    direction: str = Field(pattern="^(credit|debit)$")

    # StrictInt: a float here is a 422, not a silent truncation. `2000000.0`
    # arriving as a float would mean something upstream did money arithmetic in
    # floating point, and swallowing it would hide that.
    amount_minor: StrictInt
    balance_after_minor: StrictInt | None = None

    currency: str = Field(min_length=1, max_length=8)
    counterparty: str | None = Field(default=None, max_length=512)
    reference: str | None = Field(default=None, max_length=128)

    # Epoch milliseconds.
    occurred_at: StrictInt
    created_at: StrictInt
    updated_at: StrictInt
    deleted_at: StrictInt | None = None

    category: str | None = Field(default=None, max_length=32)
    confidence: Confidence
    parser_version: StrictInt = Field(ge=0)
    source: str = Field(pattern="^(parsed|manual)$")

    # The client stores this as INTEGER 0/1. Pydantic maps those to bool for us,
    # but StrictBool would reject them, so this stays a plain bool.
    is_user_edited: bool


class SyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Opaque to the client. "0" or omitted on first sync.
    cursor: str = "0"
    changes: list[TransactionDTO] = Field(default_factory=list, max_length=SETTINGS.SYNC_MAX_BATCH)


class SyncResponse(BaseModel):
    cursor: str
    changes: list[TransactionDTO]
    server_time: int
    # True when the pull hit SYNC_MAX_PULL and more rows are waiting. The client
    # should sync again immediately with the returned cursor rather than waiting
    # for its next scheduled sync.
    has_more: bool = False


class SharedTransactions(BaseModel):

    uuid: str = Field(min_length=1, max_length=36)


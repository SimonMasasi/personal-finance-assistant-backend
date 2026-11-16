"""The single place epoch milliseconds cross into `datetime` and back.

The client stores every timestamp as epoch **milliseconds** (`Date.now()`), and
Postgres stores them as `TIMESTAMPTZ`. Getting that conversion wrong by a factor
of 1000 is silent — no error, no exception, just every chart in the app plotting
dates in 1970 or the far future. Keeping both directions in one small tested
module is the mitigation: there is exactly one line to audit, and
`src/tests/test_time.py` round-trips a real millisecond value through it.

`TIMESTAMPTZ` has microsecond precision, so milliseconds round-trip exactly.
"""

from datetime import UTC, datetime

# Sanity bounds. Anything outside this range is a unit mistake, not a date.
#
# The lower bound is 2000-01-01 rather than 0 specifically to catch a *seconds*
# value passed where milliseconds were expected. Seconds timestamps for any date
# between 2001 and 2100 fall in 1.0e9..4.1e9, which read as milliseconds land in
# January and February 1970 — comfortably below this bound. A bound of 0 would
# accept them silently, which is exactly the off-by-1000 this module exists to
# prevent. No transaction parsed from a phone notification predates 2000, so
# nothing legitimate is excluded.
MIN_EPOCH_MS = 946_684_800_000  # 2000-01-01T00:00:00Z

# The upper bound catches microseconds or nanoseconds passed by mistake.
MAX_EPOCH_MS = 4_102_444_800_000  # 2100-01-01T00:00:00Z


class EpochRangeError(ValueError):
    """Raised when a millisecond value is implausible enough to be a unit bug."""


def ms_to_dt(ms: int) -> datetime:
    """Convert epoch milliseconds to a UTC-aware datetime."""
    if not isinstance(ms, int) or isinstance(ms, bool):
        raise EpochRangeError(f"expected int epoch milliseconds, got {type(ms).__name__}")
    if ms < MIN_EPOCH_MS or ms > MAX_EPOCH_MS:
        raise EpochRangeError(
            f"{ms} is outside the plausible epoch-millisecond range "
            f"[{MIN_EPOCH_MS}, {MAX_EPOCH_MS}] — likely seconds or microseconds"
        )
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


def dt_to_ms(dt: datetime) -> int:
    """Convert a datetime to epoch milliseconds.

    A naive datetime is treated as UTC. Postgres hands back aware datetimes for
    `TIMESTAMPTZ`, but a naive one can reach here from a default or a test.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    # round() rather than int(): float division in ms_to_dt can land a hair
    # under the exact microsecond, and truncating would lose the final
    # millisecond on the way back.
    return round(dt.timestamp() * 1000)


def ms_to_dt_optional(ms: int | None) -> datetime | None:
    return None if ms is None else ms_to_dt(ms)


def dt_to_ms_optional(dt: datetime | None) -> int | None:
    return None if dt is None else dt_to_ms(dt)


def now_ms() -> int:
    """Current server time as epoch milliseconds, for the sync response."""
    return dt_to_ms(datetime.now(tz=UTC))

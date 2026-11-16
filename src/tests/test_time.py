"""The off-by-1000 guard. This module is small precisely so it can be audited."""

from datetime import UTC, datetime

import pytest

from src.shared.time import (
    EpochRangeError,
    dt_to_ms,
    dt_to_ms_optional,
    ms_to_dt,
    ms_to_dt_optional,
    now_ms,
)


class TestRoundTrip:
    @pytest.mark.parametrize(
        "ms",
        [
            1_786_000_000_123,  # the acceptance-criteria value
            1_786_000_000_000,
            1_786_000_000_999,  # every millisecond in the final digit
            946_684_800_000,  # exactly the lower bound
            4_102_444_799_999,  # one below the upper bound
        ],
    )
    def test_milliseconds_survive_exactly(self, ms: int) -> None:
        assert dt_to_ms(ms_to_dt(ms)) == ms

    def test_every_final_digit_round_trips(self) -> None:
        """Guards against float division losing the last millisecond."""
        base = 1_786_000_000_000
        for offset in range(1000):
            assert dt_to_ms(ms_to_dt(base + offset)) == base + offset


class TestRangeGuards:
    def test_a_seconds_value_is_rejected(self) -> None:
        """1786000000 is a valid *seconds* timestamp. As ms it is 1970."""
        with pytest.raises(EpochRangeError):
            ms_to_dt(1_786_000_000)

    def test_a_microseconds_value_is_rejected(self) -> None:
        with pytest.raises(EpochRangeError):
            ms_to_dt(1_786_000_000_123_000)

    def test_zero_is_rejected(self) -> None:
        with pytest.raises(EpochRangeError):
            ms_to_dt(0)

    def test_negative_is_rejected(self) -> None:
        with pytest.raises(EpochRangeError):
            ms_to_dt(-1)

    def test_a_float_is_rejected(self) -> None:
        """Money and time both refuse floats; a float here means something
        upstream did arithmetic it should not have."""
        with pytest.raises(EpochRangeError):
            ms_to_dt(1_786_000_000_123.5)  # type: ignore[arg-type]

    def test_a_bool_is_rejected(self) -> None:
        """bool is an int subclass in Python; True must not become 1970."""
        with pytest.raises(EpochRangeError):
            ms_to_dt(True)


class TestConversion:
    def test_ms_to_dt_is_utc_aware(self) -> None:
        assert ms_to_dt(1_786_000_000_123).tzinfo is not None

    def test_naive_datetime_is_treated_as_utc(self) -> None:
        naive = datetime(2026, 8, 7, 12, 0, 0)
        aware = datetime(2026, 8, 7, 12, 0, 0, tzinfo=UTC)
        assert dt_to_ms(naive) == dt_to_ms(aware)

    def test_optionals_pass_none_through(self) -> None:
        assert ms_to_dt_optional(None) is None
        assert dt_to_ms_optional(None) is None
        assert ms_to_dt_optional(1_786_000_000_123) is not None

    def test_now_ms_is_in_the_millisecond_range(self) -> None:
        value = now_ms()
        assert 1_700_000_000_000 < value < 4_102_444_800_000
        assert isinstance(value, int)

import logging

from src.core.logging import JsonFormatter, RedactingFilter, redact


class TestRedaction:
    def test_bearer_tokens_are_redacted(self) -> None:
        out = redact("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abcdefghij.signature")
        assert "eyJhbGci" not in out
        assert "REDACTED" in out

    def test_bare_jwts_are_redacted(self) -> None:
        assert "eyJ" not in redact("token=eyJhbGciOiJIUzI1NiJ9.payload.sig")

    def test_passwords_are_redacted(self) -> None:
        assert "hunter2" not in redact('{"password": "hunter2"}')

    def test_refresh_tokens_are_redacted(self) -> None:
        assert "abc123secret" not in redact('{"refresh": "abc123secret"}')

    def test_access_tokens_are_redacted(self) -> None:
        assert "sekrit-value" not in redact('{"access": "sekrit-value"}')

    def test_counterparty_is_redacted(self) -> None:
        """Free text lifted verbatim from a bank SMS. It names real people and
        is the closest thing here to the raw message text the design keeps on
        the device."""
        assert "JOHN MWANGI" not in redact('{"counterparty": "JOHN MWANGI"}')

    def test_ordinary_text_is_untouched(self) -> None:
        message = "Synced 3 rows for user 0e768cd7 at cursor 42"
        assert redact(message) == message

    def test_amounts_are_not_redacted(self) -> None:
        """Redaction must not eat the fields that make logs useful."""
        message = '{"amount_minor": 2000000, "provider": "mpesa"}'
        assert redact(message) == message


class TestRedactingFilter:
    def _record(self, msg: str, *args: object) -> logging.LogRecord:
        return logging.LogRecord("t", logging.INFO, __file__, 1, msg, args, None)

    def test_filter_redacts_the_interpolated_message(self) -> None:
        """A token passed as a %s argument must not survive.

        Checking record.msg alone would miss it, which is why the filter renders
        first and redacts the result.
        """
        record = self._record("token is %s", "eyJhbGciOiJIUzI1NiJ9.payload.sig")
        RedactingFilter().filter(record)
        assert "eyJ" not in record.getMessage()

    def test_filter_returns_true_so_the_record_is_still_emitted(self) -> None:
        record = self._record("hello")
        assert RedactingFilter().filter(record) is True


class TestJsonFormatter:
    def test_output_is_json_with_the_expected_keys(self) -> None:
        import json

        record = logging.LogRecord("t", logging.INFO, __file__, 1, "hello", (), None)
        payload = json.loads(JsonFormatter().format(record))

        assert payload["level"] == "INFO"
        assert payload["message"] == "hello"
        assert "time" in payload

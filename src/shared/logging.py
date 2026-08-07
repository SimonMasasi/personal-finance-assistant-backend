"""Structured JSON logging with hard redaction.

Two categories must never reach a log line:

  - credentials — passwords, bearer tokens, refresh tokens
  - `counterparty` — free text lifted verbatim from a bank SMS. It is the
    closest thing in this system to the raw message text the whole design
    exists to keep on the device, and it names real people.

The filter below is a backstop, not a licence to log these deliberately.
"""

import json
import logging
import re
from typing import Any

_REDACTIONS = (
    # Bearer tokens in a header dump or a URL.
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 [REDACTED]"),
    # JSON-ish "key": "value" for any sensitive key.
    (
        re.compile(
            r'(?i)(["\']?(?:password|passwd|token|access|refresh|authorization|'
            r'secret|password_hash|counterparty)["\']?\s*[:=]\s*)'
            r'(["\'])(?:\\.|[^"\'\\])*\2'
        ),
        r"\1\2[REDACTED]\2",
    ),
    # Bare JWTs anywhere in the message.
    (re.compile(r"\beyJ[A-Za-z0-9._-]{10,}"), "[REDACTED]"),
)


def redact(text: str) -> str:
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Render the message here so redaction sees the interpolated result;
        # a token passed as a %s arg would survive a check of record.msg alone.
        try:
            rendered = record.getMessage()
        except Exception:
            return True
        record.msg = redact(rendered)
        record.args = ()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
        }
        if record.exc_info:
            payload["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload)


def configure_logging(debug: bool) -> None:
    handler = logging.StreamHandler()
    handler.addFilter(RedactingFilter())
    if not debug:
        # Human-readable locally, machine-readable in deployment.
        handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.DEBUG if debug else logging.INFO)

    # uvicorn installs its own handlers; strip them so nothing bypasses the
    # redacting filter. access logs in particular echo full request lines.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True

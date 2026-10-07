"""Structured JSON logging with a request ID and secret redaction.

Policy: logs carry request IDs, status, duration and provider usage only.
Request/response bodies, bearer tokens, connection strings, source text and
generated documents are never passed to a logger. The redaction filter is a
second line of defence in case a library or a future change logs one anyway.
"""

import json
import logging
import re
import sys
import traceback
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

# Set by RequestContextMiddleware for the duration of each request.
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

HANDLER_NAME = "resume-tailor-json"

# Libraries that log request URLs or payload details at INFO/DEBUG; only their
# warnings and errors are let through. "httpx2"/"httpcore2" are what the
# OpenAI SDK sends its requests with, "httpx"/"httpcore" what other clients use.
QUIET_LIBRARY_LOGGERS = ("httpx", "httpx2", "httpcore", "httpcore2", "openai", "pymongo")

_REDACTIONS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer [REDACTED]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"), "[REDACTED_API_KEY]"),
    (re.compile(r"mongodb(?:\+srv)?://[^\s\"'<>]+"), "[REDACTED_MONGODB_URI]"),
]


def redact(text: str) -> str:
    """Mask bearer tokens, OpenAI-style keys and MongoDB connection strings."""
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def _redact_value(value: Any) -> Any:
    """Apply redact() to every string inside a (possibly nested) log field value."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {key: _redact_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_value(item) for item in value]
    return value


class RedactionFilter(logging.Filter):
    """Rewrites each record so the message, structured fields and any exception
    text are redacted before a formatter sees them."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = None
        fields = getattr(record, "fields", None)
        if fields:
            record.fields = _redact_value(fields)
        if record.exc_info:
            record.exc_text = redact("".join(traceback.format_exception(*record.exc_info)))
            record.exc_info = None
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line: timestamp, level, logger, message, request_id
    and any structured fields attached with log_event()."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        payload.update(getattr(record, "fields", None) or {})
        if record.exc_text:
            payload["exception"] = record.exc_text
        return json.dumps(payload, default=str)


def log_event(logger: logging.Logger, level: int, message: str, **fields: Any) -> None:
    """Log ``message`` with structured fields that become top-level JSON keys."""
    logger.log(level, message, extra={"fields": fields})


def exception_location(error: BaseException) -> list[str]:
    """Where an exception was raised, as "file:line:function" frames.

    Used instead of a full traceback because exception messages can contain
    user text (for example a validation error quoting its input).
    """
    frames = traceback.extract_tb(error.__traceback__)
    return [f"{frame.filename}:{frame.lineno}:{frame.name}" for frame in frames[-6:]]


def configure_logging(level: int = logging.INFO) -> None:
    """Install the JSON handler on the root logger (idempotent).

    Uvicorn's own handlers are removed so its messages go through the same
    formatter and filter. Its access log is disabled because it prints raw
    paths and query strings; RequestContextMiddleware writes the access log.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.set_name(HANDLER_NAME)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactionFilter())

    root = logging.getLogger()
    for existing in list(root.handlers):
        if existing.get_name() == HANDLER_NAME:
            root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    logging.getLogger("uvicorn.access").disabled = True

    for name in QUIET_LIBRARY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

"""JSON log format and redaction of secrets."""

import io
import json
import logging

import pytest

from app.logging_config import (
    JsonFormatter,
    RedactionFilter,
    exception_location,
    log_event,
    redact,
    request_id_var,
)

TOKEN = "q1w2e3r4t5y6u7i8o9p0a1s2d3f4g5h6j7k8l9z0x1c"
API_KEY = "sk-proj-AbCdEf0123456789AbCdEf0123456789"
MONGO_URI = "mongodb+srv://app_user:s3cretPass@cluster0.example.mongodb.net/resume?retryWrites=true"


@pytest.fixture
def log_output() -> tuple[logging.Logger, io.StringIO]:
    """A logger wired exactly like the application's handler, writing to memory."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactionFilter())
    logger = logging.getLogger("test.redaction")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    return logger, stream


def last_line(stream: io.StringIO) -> dict:
    return json.loads(stream.getvalue().strip().splitlines()[-1])


def test_redact_masks_bearer_tokens_api_keys_and_mongodb_uris() -> None:
    text = f"Authorization: Bearer {TOKEN} key={API_KEY} db={MONGO_URI} done"
    redacted = redact(text)
    assert TOKEN not in redacted
    assert API_KEY not in redacted
    assert "s3cretPass" not in redacted
    assert "cluster0" not in redacted
    assert redacted.endswith(" done")
    assert "Bearer [REDACTED]" in redacted
    assert "[REDACTED_API_KEY]" in redacted
    assert "[REDACTED_MONGODB_URI]" in redacted


def test_redact_leaves_ordinary_text_alone() -> None:
    text = "request finished status=200 duration_ms=12.5 route=/api/jobs/{job_id}"
    assert redact(text) == text


def test_plain_mongodb_uri_without_credentials_is_also_masked() -> None:
    assert redact("connect mongodb://127.0.0.1:27017 ok") == "connect [REDACTED_MONGODB_URI] ok"


def test_secrets_in_message_arguments_are_redacted(
    log_output: tuple[logging.Logger, io.StringIO],
) -> None:
    logger, stream = log_output
    logger.info("header was %s and key %s", f"Bearer {TOKEN}", API_KEY)
    line = stream.getvalue()
    assert TOKEN not in line
    assert API_KEY not in line
    assert last_line(stream)["message"] == "header was Bearer [REDACTED] and key [REDACTED_API_KEY]"


def test_secrets_in_structured_fields_are_redacted(
    log_output: tuple[logging.Logger, io.StringIO],
) -> None:
    logger, stream = log_output
    log_event(
        logger,
        logging.INFO,
        "event",
        uri=MONGO_URI,
        nested={"auth": f"Bearer {TOKEN}", "keys": [API_KEY]},
        status=200,
    )
    line = stream.getvalue()
    assert "s3cretPass" not in line
    assert TOKEN not in line
    assert API_KEY not in line
    payload = last_line(stream)
    assert payload["uri"] == "[REDACTED_MONGODB_URI]"
    assert payload["nested"] == {"auth": "Bearer [REDACTED]", "keys": ["[REDACTED_API_KEY]"]}
    assert payload["status"] == 200


def test_secrets_in_exception_text_are_redacted(
    log_output: tuple[logging.Logger, io.StringIO],
) -> None:
    logger, stream = log_output
    try:
        raise RuntimeError(f"could not connect to {MONGO_URI}")
    except RuntimeError:
        logger.exception("startup failed")
    line = stream.getvalue()
    assert "s3cretPass" not in line
    payload = last_line(stream)
    assert "RuntimeError" in payload["exception"]
    assert "[REDACTED_MONGODB_URI]" in payload["exception"]


def test_log_lines_are_json_with_request_id_and_fields(
    log_output: tuple[logging.Logger, io.StringIO],
) -> None:
    logger, stream = log_output
    context_token = request_id_var.set("req-123")
    try:
        log_event(logger, logging.INFO, "request", method="GET", route="/healthz", status=200)
    finally:
        request_id_var.reset(context_token)
    payload = last_line(stream)
    assert payload["message"] == "request"
    assert payload["request_id"] == "req-123"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "test.redaction"
    assert payload["method"] == "GET"
    assert payload["route"] == "/healthz"
    assert payload["status"] == 200
    assert payload["ts"].endswith("+00:00")


def test_exception_location_reports_frames_without_the_message() -> None:
    try:
        raise ValueError("resume text: Jordan Rivera, 412-555-0142")
    except ValueError as error:
        frames = exception_location(error)
    assert frames
    assert all("Jordan" not in frame for frame in frames)
    assert frames[-1].endswith(":test_exception_location_reports_frames_without_the_message")

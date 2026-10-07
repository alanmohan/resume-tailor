"""Small assertion helpers shared by the integration tests."""

from datetime import datetime

import httpx


def assert_error(response: httpx.Response, status: int, code: str) -> dict:
    """Check the status code and the error envelope, and that the request ID in
    the body matches the X-Request-ID header. Returns the ``error`` object."""
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"error"}
    error = body["error"]
    assert error["code"] == code
    assert error["message"]
    assert error["request_id"] == response.headers["x-request-id"]
    assert set(error) <= {"code", "message", "request_id", "field_errors", "retryable", "details"}
    return error


def parse_iso_z(value: str) -> datetime:
    """Parse an API timestamp, insisting on the ISO-8601 UTC "Z" form."""
    assert value.endswith("Z"), value
    return datetime.fromisoformat(value.replace("Z", "+00:00"))

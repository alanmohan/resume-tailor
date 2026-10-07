"""Every kind of failure produces the same error envelope.

The application's real routes are covered elsewhere; here a few probe routes
are added to a test app so each error path can be triggered on demand.
"""

import asyncio
import json
from collections.abc import Awaitable, Callable

import httpx
import pytest
from fastapi import FastAPI
from pymongo.errors import ServerSelectionTimeoutError

from app.api.deps import IdempotencyKeyDep
from app.config import Settings
from app.errors import GenerationInProgress, NotFound, ValidationFailed, VersionConflict
from app.providers.base import (
    ProviderInvalidOutput,
    ProviderNotConfigured,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderUnavailable,
)
from app.schemas.profiles import IngestRequest
from tests.conftest import TEST_ORIGIN, client_for
from tests.helpers import assert_error

MakeApp = Callable[[Settings], Awaitable[FastAPI]]
MakeSettings = Callable[..., Settings]

SECRET_DETAIL = "resume of Jordan Rivera, phone 412-555-0142"

PROVIDER_FAILURES = {
    "timeout": ProviderTimeout("The AI provider took too long to respond."),
    "rate-limited": ProviderRateLimited("The AI provider is rate limiting requests."),
    "unavailable": ProviderUnavailable("Could not reach the AI provider."),
    "invalid-output": ProviderInvalidOutput("The AI provider returned an invalid response."),
    "not-configured": ProviderNotConfigured(),
}

APP_FAILURES = {
    "not-found": NotFound(),
    "version-conflict": VersionConflict(),
    "in-progress": GenerationInProgress("gen-123"),
    "field": ValidationFailed.for_field("sources", "At most 5 sources are allowed"),
}


@pytest.fixture
def probe_app(app: FastAPI) -> FastAPI:
    """The test app plus routes that fail in specific ways."""

    @app.post("/api/_probe/validate")
    async def validate(body: IngestRequest) -> dict[str, int]:
        return {"sources": len(body.sources)}

    @app.get("/api/_probe/boom")
    async def boom() -> None:
        raise RuntimeError(SECRET_DETAIL)

    @app.get("/api/_probe/provider/{kind}")
    async def provider_failure(kind: str) -> None:
        raise PROVIDER_FAILURES[kind]

    @app.get("/api/_probe/app/{kind}")
    async def app_failure(kind: str) -> None:
        raise APP_FAILURES[kind]

    @app.get("/api/_probe/database")
    async def database_failure() -> None:
        raise ServerSelectionTimeoutError("mongodb://user:hunter2@db.example:27017 timed out")

    @app.post("/api/_probe/idempotent")
    async def idempotent(key: IdempotencyKeyDep) -> dict[str, str]:
        return {"key": key}

    return app


@pytest.fixture
def probe(probe_app: FastAPI, client: httpx.AsyncClient) -> httpx.AsyncClient:
    return client


async def test_unknown_route_returns_not_found_envelope(client: httpx.AsyncClient) -> None:
    error = assert_error(await client.get("/api/does-not-exist"), 404, "not_found")
    assert set(error) == {"code", "message", "request_id"}
    assert_error(await client.get("/nowhere"), 404, "not_found")


async def test_wrong_method_returns_method_not_allowed_envelope(client: httpx.AsyncClient) -> None:
    response = await client.put("/api/session")
    assert_error(response, 405, "method_not_allowed")
    assert "GET" in response.headers["allow"]


async def test_schema_violations_return_field_errors(probe: httpx.AsyncClient) -> None:
    body = {
        "sources": [
            {"label": "x" * 81, "source_type": "resume", "text": "Jordan Rivera"},
            {"label": "Notes", "source_type": "diary", "text": "   "},
        ]
    }
    error = assert_error(
        await probe.post("/api/_probe/validate", json=body), 422, "validation_error"
    )

    fields = {item["field"]: item["message"] for item in error["field_errors"]}
    assert set(fields) == {"sources.0.label", "sources.1.source_type", "sources.1.text"}
    assert "80 characters" in fields["sources.0.label"]
    assert fields["sources.1.text"] == "Source text must not be empty"
    # The rejected input is never echoed back.
    assert "Jordan Rivera" not in json.dumps(error)


async def test_missing_body_field_is_reported_by_name(probe: httpx.AsyncClient) -> None:
    error = assert_error(await probe.post("/api/_probe/validate", json={}), 422, "validation_error")
    assert error["field_errors"] == [{"field": "sources", "message": "Field required"}]


async def test_invalid_json_is_a_validation_error(probe: httpx.AsyncClient) -> None:
    response = await probe.post(
        "/api/_probe/validate",
        content=b'{"sources": [',
        headers={"Content-Type": "application/json"},
    )
    error = assert_error(response, 422, "validation_error")
    assert error["field_errors"][0]["field"] == "body"


async def test_unexpected_exception_returns_internal_error_without_details(
    probe: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO")
    response = await probe.get("/api/_probe/boom", headers={"Origin": TEST_ORIGIN})

    error = assert_error(response, 500, "internal_error")
    assert error["message"] == "Something went wrong on the server."
    assert "Jordan" not in response.text and "RuntimeError" not in response.text
    # The browser can read the error because CORS headers are still applied.
    assert response.headers["access-control-allow-origin"] == TEST_ORIGIN

    records = [r for r in caplog.records if r.getMessage() == "unhandled_exception"]
    assert len(records) == 1
    assert records[0].fields["error_type"] == "RuntimeError"
    assert any("boom" in frame for frame in records[0].fields["location"])
    # Outside production the traceback is logged so the bug can be diagnosed.
    # (The redaction filter has already turned it into text on the record.)
    assert "RuntimeError" in records[0].exc_text
    assert "in boom" in records[0].exc_text


async def test_production_logs_omit_exception_messages(
    make_app: MakeApp, make_settings: MakeSettings, caplog: pytest.LogCaptureFixture
) -> None:
    """Exception messages can quote user content, so production logs carry only
    the exception type and where it was raised."""
    application = await make_app(
        make_settings(
            app_env="production",
            ai_provider="openai",
            openai_api_key="placeholder-key-for-tests",
            cors_origins=[TEST_ORIGIN],
        )
    )

    @application.get("/api/_probe/boom")
    async def boom() -> None:
        raise RuntimeError(SECRET_DETAIL)

    caplog.set_level("INFO")
    async with client_for(application) as production_client:
        response = await production_client.get("/api/_probe/boom")

    assert_error(response, 500, "internal_error")
    assert SECRET_DETAIL not in response.text
    records = [r for r in caplog.records if r.getMessage() == "unhandled_exception"]
    assert len(records) == 1
    assert records[0].fields["error_type"] == "RuntimeError"
    assert any("boom" in frame for frame in records[0].fields["location"])
    for record in caplog.records:
        assert record.exc_info is None and record.exc_text is None
        assert SECRET_DETAIL not in record.getMessage()
        assert SECRET_DETAIL not in repr(getattr(record, "fields", ""))


@pytest.mark.parametrize(
    ("kind", "status", "code", "retryable"),
    [
        ("timeout", 504, "provider_timeout", True),
        ("rate-limited", 503, "provider_rate_limited", True),
        ("unavailable", 502, "provider_unavailable", True),
        ("invalid-output", 502, "provider_invalid_output", True),
        ("not-configured", 502, "provider_unavailable", False),
    ],
)
async def test_provider_errors_map_to_their_status_and_code(
    probe: httpx.AsyncClient, kind: str, status: int, code: str, retryable: bool
) -> None:
    error = assert_error(await probe.get(f"/api/_probe/provider/{kind}"), status, code)
    assert error["retryable"] is retryable
    assert error["message"] == PROVIDER_FAILURES[kind].message


async def test_application_errors_keep_their_code_details_and_field_errors(
    probe: httpx.AsyncClient,
) -> None:
    assert_error(await probe.get("/api/_probe/app/not-found"), 404, "not_found")
    assert_error(await probe.get("/api/_probe/app/version-conflict"), 409, "version_conflict")

    in_progress = assert_error(
        await probe.get("/api/_probe/app/in-progress"), 409, "generation_in_progress"
    )
    assert in_progress["details"] == {"generation_id": "gen-123"}

    invalid = assert_error(await probe.get("/api/_probe/app/field"), 422, "validation_error")
    assert invalid["field_errors"] == [
        {"field": "sources", "message": "At most 5 sources are allowed"}
    ]


async def test_database_connection_failure_returns_database_unavailable(
    probe: httpx.AsyncClient,
) -> None:
    response = await probe.get("/api/_probe/database")
    error = assert_error(response, 503, "database_unavailable")
    assert error["retryable"] is True
    assert "hunter2" not in response.text and "mongodb://" not in response.text


@pytest.mark.parametrize("key", [None, "short", "x" * 129, "has spaces in it"])
async def test_missing_or_invalid_idempotency_key_is_rejected(
    probe: httpx.AsyncClient, key: str | None
) -> None:
    headers = {"Idempotency-Key": key} if key else {}
    response = await probe.post("/api/_probe/idempotent", headers=headers)
    assert_error(response, 400, "idempotency_key_required")


async def test_valid_idempotency_key_is_passed_through(probe: httpx.AsyncClient) -> None:
    key = "0b9f6c2e-7d2a-4a53-9c1e-5f6d7a8b9c0d"
    response = await probe.post("/api/_probe/idempotent", headers={"Idempotency-Key": key})
    assert response.status_code == 200
    assert response.json() == {"key": key}


async def test_every_response_has_a_unique_request_id(client: httpx.AsyncClient) -> None:
    first = await client.get("/healthz")
    second = await client.get("/healthz")
    assert len(first.headers["x-request-id"]) == 32
    assert first.headers["x-request-id"] != second.headers["x-request-id"]


async def test_access_log_records_the_route_template_not_the_url(
    probe: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO", logger="app.middleware")
    response = await probe.get("/api/_probe/provider/timeout?token=should-not-be-logged")

    records = [r for r in caplog.records if r.getMessage() == "request"]
    assert len(records) == 1
    fields = records[0].fields
    assert fields["method"] == "GET"
    assert fields["route"] == "/api/_probe/provider/{kind}"
    assert fields["status"] == 504
    assert fields["duration_ms"] >= 0
    assert set(fields) == {"method", "route", "status", "duration_ms"}
    assert "should-not-be-logged" not in repr(fields)
    assert response.headers["x-request-id"]


async def test_access_log_names_documentation_pages_and_hides_unknown_urls(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO", logger="app.middleware")
    await client.get("/docs")
    await client.get("/api/jobs/5f2b1c0d9e8a4b7c8d6e5f4a3b2c1d0e/secret-looking-path")

    routes = [r.fields["route"] for r in caplog.records if r.getMessage() == "request"]
    assert routes == ["/docs", "unmatched"]


async def test_in_flight_work_finishes_when_the_client_disconnects(app: FastAPI) -> None:
    """A disconnect must not cancel the handler: with pure ASGI middleware the
    endpoint runs to completion (an aborted provider call or half-finished
    write would otherwise be possible)."""
    finished = asyncio.Event()

    @app.post("/api/_probe/slow")
    async def slow() -> dict[str, bool]:
        await asyncio.sleep(0.05)
        finished.set()
        return {"done": True}

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/_probe/slow",
        "raw_path": b"/api/_probe/slow",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"testserver")],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }
    incoming = [
        {"type": "http.request", "body": b"", "more_body": False},
        {"type": "http.disconnect"},
    ]
    sent: list[dict] = []

    async def receive() -> dict:
        return incoming.pop(0) if incoming else {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        sent.append(message)

    await app(scope, receive, send)

    assert finished.is_set()
    assert sent[0]["type"] == "http.response.start" and sent[0]["status"] == 200

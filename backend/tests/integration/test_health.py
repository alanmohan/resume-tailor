"""Liveness and readiness, including a database that cannot be reached."""

from collections.abc import Awaitable, Callable

import httpx
from fastapi import FastAPI

from app.config import Settings
from app.providers.fake.provider import FakeProvider
from tests.conftest import client_for
from tests.helpers import assert_error

MakeApp = Callable[[Settings], Awaitable[FastAPI]]
MakeSettings = Callable[..., Settings]

# Nothing listens on port 9 (the "discard" port), so connections fail quickly.
UNREACHABLE_URI = "mongodb://127.0.0.1:9"
# The non-sensitive input limits, reported for the Start page (SPEC-05).
DEFAULT_LIMITS = {
    "max_profile_chars": 60_000,
    "max_job_chars": 25_000,
    "max_sources": 5,
    "max_requirements": 25,
    "session_ttl_hours": 24,
}


async def test_healthz_reports_ok(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readyz_reports_ready_without_calling_the_provider(
    client: httpx.AsyncClient, fake_provider: FakeProvider
) -> None:
    response = await client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"database": "ok", "provider": "configured"},
        "provider_mode": "fake",
        "limits": DEFAULT_LIMITS,
    }
    assert sum(fake_provider.calls.values()) == 0


async def test_health_endpoints_do_not_expose_configuration(client: httpx.AsyncClient) -> None:
    for path in ("/healthz", "/readyz"):
        text = (await client.get(path)).text
        assert "mongodb" not in text
        assert "127.0.0.1" not in text
        assert "resume_tailor" not in text


async def test_unreachable_database_keeps_liveness_but_fails_readiness(
    make_app: MakeApp, make_settings: MakeSettings
) -> None:
    settings = make_settings(mongodb_uri=UNREACHABLE_URI, mongodb_server_selection_timeout_ms=200)
    # Startup itself must succeed although the database is down.
    application = await make_app(settings)

    async with client_for(application) as client:
        health = await client.get("/healthz")
        ready = await client.get("/readyz")
        create = await client.post("/api/sessions")

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert ready.status_code == 503
    assert ready.json() == {
        "status": "not_ready",
        "checks": {"database": "unavailable", "provider": "configured"},
        "provider_mode": "fake",
        "limits": DEFAULT_LIMITS,
    }
    error = assert_error(create, 503, "database_unavailable")
    assert error["retryable"] is True
    assert application.state.mongo.indexes_ready is False


async def test_missing_provider_key_fails_readiness_only(
    make_app: MakeApp, make_settings: MakeSettings
) -> None:
    application = await make_app(make_settings(ai_provider="openai", openai_api_key=None))

    async with client_for(application) as client:
        health = await client.get("/healthz")
        ready = await client.get("/readyz")

    assert health.status_code == 200
    assert ready.status_code == 503
    assert ready.json() == {
        "status": "not_ready",
        "checks": {"database": "ok", "provider": "not_configured"},
        "provider_mode": "openai",
        "limits": DEFAULT_LIMITS,
    }


async def test_readiness_check_creates_indexes_that_startup_could_not(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    # Simulate a start during which the database was down.
    app.state.mongo.indexes_ready = False

    response = await client.get("/readyz")

    assert response.status_code == 200
    assert app.state.mongo.indexes_ready is True


async def test_first_database_request_creates_indexes_that_startup_could_not(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    app.state.mongo.indexes_ready = False
    assert (await client.post("/api/sessions")).status_code == 201
    assert app.state.mongo.indexes_ready is True


async def test_api_docs_are_served(client: httpx.AsyncClient) -> None:
    assert (await client.get("/docs")).status_code == 200
    schema = (await client.get("/openapi.json")).json()
    assert {"/healthz", "/readyz", "/api/sessions", "/api/session"} <= set(schema["paths"])
    assert "HTTPBearer" in schema["components"]["securitySchemes"]

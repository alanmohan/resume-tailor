"""SPEC-05: GET /readyz reports the input limits.

The Start page is shown before a session exists, so it had no source for the
real limits and used hard-coded ones (60,000 characters, 24 hours) whatever
the server was configured with.
"""

from collections.abc import Awaitable, Callable

from fastapi import FastAPI

from app.config import Settings
from tests.conftest import client_for

MakeApp = Callable[[Settings], Awaitable[FastAPI]]
MakeSettings = Callable[..., Settings]

LIMIT_FIELDS = {
    "max_profile_chars",
    "max_job_chars",
    "max_sources",
    "max_requirements",
    "session_ttl_hours",
}


async def test_readyz_reports_the_configured_limits_without_a_session(
    make_app: MakeApp, make_settings: MakeSettings
) -> None:
    application = await make_app(
        make_settings(max_profile_chars=30_000, session_ttl_hours=72, max_sources=3)
    )
    async with client_for(application) as client:
        ready = await client.get("/readyz")
        session = await client.post("/api/sessions")

    assert ready.status_code == 200
    body = ready.json()
    assert set(body) == {"status", "checks", "provider_mode", "limits"}
    assert body["limits"] == {
        "max_profile_chars": 30_000,
        "max_job_chars": 25_000,
        "max_sources": 3,
        "max_requirements": 25,
        "session_ttl_hours": 72,
    }
    # The same object a session reports, so the page shows one set of numbers.
    assert body["limits"] == session.json()["limits"]


async def test_limits_are_reported_even_when_the_service_is_not_ready(
    make_app: MakeApp, make_settings: MakeSettings
) -> None:
    application = await make_app(make_settings(ai_provider="openai", openai_api_key=None))
    async with client_for(application) as client:
        ready = await client.get("/readyz")

    assert ready.status_code == 503
    assert set(ready.json()["limits"]) == LIMIT_FIELDS

"""Every profile, evidence and job route requires a live session token."""

from collections.abc import Awaitable, Callable
from datetime import timedelta

import httpx
import pytest

from app.db import Database
from app.providers.fake.provider import FakeProvider
from app.schemas.common import utc_now
from tests.conftest import SessionHandle
from tests.helpers import assert_error

CreateSession = Callable[[], Awaitable[SessionHandle]]

ROUTES = [
    ("POST", "/api/profiles/ingest"),
    ("GET", "/api/profile"),
    ("PATCH", "/api/profile"),
    ("POST", "/api/profile/confirm"),
    ("GET", "/api/evidence/0123456789abcdef0123456789abcdef"),
    ("POST", "/api/jobs"),
    ("GET", "/api/jobs"),
    ("GET", "/api/jobs/0123456789abcdef0123456789abcdef"),
    ("PATCH", "/api/jobs/0123456789abcdef0123456789abcdef"),
]
# A well-formed body for the routes that take one, so a 401 cannot be mistaken
# for a validation error.
BODIES = {
    "/api/profiles/ingest": {
        "sources": [{"label": "Resume", "source_type": "resume", "text": "Experience\n"}]
    },
    "/api/profile": {"expected_version": 1, "contact": {}, "records": []},
    "/api/profile/confirm": {"expected_version": 1},
    "/api/jobs": {"description": "Requirements\n- Python\n"},
    "/api/jobs/0123456789abcdef0123456789abcdef": {"expected_version": 1, "requirements": []},
}


async def call(
    client: httpx.AsyncClient, method: str, path: str, headers: dict[str, str]
) -> httpx.Response:
    body = BODIES.get(path) if method != "GET" else None
    return await client.request(method, path, json=body, headers=headers)


@pytest.mark.parametrize(("method", "path"), ROUTES)
async def test_request_without_a_valid_token_is_unauthorized(
    client: httpx.AsyncClient, fake_provider: FakeProvider, method: str, path: str
) -> None:
    assert_error(await call(client, method, path, {}), 401, "unauthorized")
    unknown = {"Authorization": "Bearer " + "a" * 43}
    assert_error(await call(client, method, path, unknown), 401, "unauthorized")
    assert_error(
        await call(client, method, path, {"Authorization": "Basic abc"}), 401, "unauthorized"
    )
    assert sum(fake_provider.calls.values()) == 0


@pytest.mark.parametrize(("method", "path"), ROUTES)
async def test_revoked_token_is_rejected(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    fake_provider: FakeProvider,
    method: str,
    path: str,
) -> None:
    session = await create_session()
    await client.delete("/api/session", headers=session.headers)

    assert_error(await call(client, method, path, session.headers), 401, "unauthorized")
    assert sum(fake_provider.calls.values()) == 0


@pytest.mark.parametrize(("method", "path"), ROUTES)
async def test_expired_token_is_rejected_before_ttl_cleanup(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    db: Database,
    fake_provider: FakeProvider,
    method: str,
    path: str,
) -> None:
    session = await create_session()
    # The session document still exists (TTL cleanup lags), but it has expired.
    await db["sessions"].update_one(
        {"_id": session.session_id}, {"$set": {"expires_at": utc_now() - timedelta(seconds=1)}}
    )

    assert_error(await call(client, method, path, session.headers), 401, "session_expired")
    assert sum(fake_provider.calls.values()) == 0
    for name in ("sources", "profiles", "jobs"):
        assert await db[name].count_documents({"owner_id": session.owner_id}) == 0

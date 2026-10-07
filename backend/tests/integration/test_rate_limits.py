"""Rate limits and quotas against the real MongoDB counters."""

import asyncio
from collections.abc import Awaitable, Callable

import httpx
import pytest
from fastapi import FastAPI

from app.config import Settings
from app.db import Database
from app.errors import QuotaExceeded, Unauthorized
from app.ratelimit import QuotaService
from app.repositories import Repositories
from app.security import SessionContext
from tests.conftest import SessionHandle, client_for
from tests.helpers import assert_error

MakeApp = Callable[[Settings], Awaitable[FastAPI]]
MakeSettings = Callable[..., Settings]
CreateSession = Callable[[], Awaitable[SessionHandle]]


def context_of(session: SessionHandle) -> SessionContext:
    return SessionContext(
        owner_id=session.owner_id, session_id=session.session_id, expires_at=session.expires_at
    )


def quota_service(repos: Repositories, settings: Settings) -> QuotaService:
    return QuotaService(repos.counters, repos.sessions, settings)


# ---- session creation --------------------------------------------------------------


async def test_session_creation_is_rate_limited_per_client(
    make_app: MakeApp, make_settings: MakeSettings
) -> None:
    application = await make_app(make_settings(session_create_limit_per_hour=3))
    async with client_for(application) as client:
        for _ in range(3):
            assert (await client.post("/api/sessions")).status_code == 201
        blocked = await client.post("/api/sessions")

    error = assert_error(blocked, 429, "rate_limited")
    assert "Too many sessions" in error["message"]
    assert 1 <= int(blocked.headers["retry-after"]) <= 3600

    db: Database = application.state.mongo.db
    assert await db["sessions"].count_documents({}) == 3
    counters = [document async for document in db["rate_limits"].find({})]
    assert len(counters) == 1
    assert counters[0]["count"] == 4
    assert counters[0]["_id"].startswith("session_create:")
    # The client address is stored only as a keyed hash.
    assert "127.0.0.1" not in counters[0]["_id"]
    assert counters[0]["expires_at"].tzinfo is not None


async def test_forwarded_address_is_used_when_proxies_are_trusted(
    make_app: MakeApp, make_settings: MakeSettings
) -> None:
    application = await make_app(
        make_settings(session_create_limit_per_hour=1, trust_proxy_headers=True)
    )
    async with client_for(application) as client:
        first = await client.post("/api/sessions", headers={"X-Forwarded-For": "198.51.100.1"})
        other = await client.post("/api/sessions", headers={"X-Forwarded-For": "198.51.100.2"})
        # The proxy appends the address it saw; whatever the client sent in
        # front of it (a forged first hop) does not give it a fresh allowance.
        again = await client.post(
            "/api/sessions", headers={"X-Forwarded-For": "10.9.8.7, 198.51.100.1"}
        )
    assert (first.status_code, other.status_code, again.status_code) == (201, 201, 429)


async def test_forwarded_address_is_ignored_when_proxies_are_not_trusted(
    make_app: MakeApp, make_settings: MakeSettings
) -> None:
    application = await make_app(make_settings(session_create_limit_per_hour=1))
    async with client_for(application) as client:
        first = await client.post("/api/sessions", headers={"X-Forwarded-For": "198.51.100.1"})
        spoofed = await client.post("/api/sessions", headers={"X-Forwarded-For": "203.0.113.50"})
    # Both requests come from the same socket address, so they share one counter.
    assert (first.status_code, spoofed.status_code) == (201, 429)


# ---- per-session quota -------------------------------------------------------------


async def test_operation_quota_is_persisted_on_the_session(
    create_session: CreateSession, repos: Repositories, db: Database, make_settings: MakeSettings
) -> None:
    session = await create_session()
    quotas = quota_service(repos, make_settings(quota_ingest=2))

    await quotas.charge_operation(context_of(session), "ingest")
    await quotas.charge_operation(context_of(session), "ingest")
    with pytest.raises(QuotaExceeded):
        await quotas.charge_operation(context_of(session), "ingest")

    stored = await db["sessions"].find_one({"_id": session.session_id})
    assert stored["quota"]["ingest"] == 2
    assert stored["quota"]["generation"] == 0
    # A new service instance (as after a restart) still sees the used quota.
    with pytest.raises(QuotaExceeded):
        await quota_service(repos, make_settings(quota_ingest=2)).charge_operation(
            context_of(session), "ingest"
        )


async def test_concurrent_requests_cannot_exceed_the_quota(
    create_session: CreateSession, repos: Repositories, db: Database, make_settings: MakeSettings
) -> None:
    session = await create_session()
    quotas = quota_service(repos, make_settings(quota_generation=4))

    results = await asyncio.gather(
        *(quotas.charge_operation(context_of(session), "generation") for _ in range(12)),
        return_exceptions=True,
    )

    assert sum(result is None for result in results) == 4
    assert sum(isinstance(result, QuotaExceeded) for result in results) == 8
    stored = await db["sessions"].find_one({"_id": session.session_id})
    assert stored["quota"]["generation"] == 4


async def test_quota_is_per_session(
    create_session: CreateSession, repos: Repositories, make_settings: MakeSettings
) -> None:
    alice = await create_session()
    bob = await create_session()
    quotas = quota_service(repos, make_settings(quota_confirm=1))

    await quotas.charge_operation(context_of(alice), "confirm")
    await quotas.charge_operation(context_of(bob), "confirm")
    with pytest.raises(QuotaExceeded):
        await quotas.charge_operation(context_of(alice), "confirm")


async def test_revoked_session_cannot_use_quota(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    repos: Repositories,
    db: Database,
    settings: Settings,
) -> None:
    session = await create_session()
    await client.delete("/api/session", headers=session.headers)

    with pytest.raises(Unauthorized):
        await quota_service(repos, settings).charge_operation(context_of(session), "ingest")

    stored = await db["sessions"].find_one({"_id": session.session_id})
    assert stored["quota"]["ingest"] == 0


# ---- global daily cap --------------------------------------------------------------


async def test_global_ai_call_cap_is_persisted_and_enforced(
    repos: Repositories, db: Database, make_settings: MakeSettings
) -> None:
    settings = make_settings(global_daily_ai_call_limit=5)
    await quota_service(repos, settings).charge_ai_calls(3)
    await quota_service(repos, settings).charge_ai_calls(2)

    with pytest.raises(QuotaExceeded) as error:
        await quota_service(repos, settings).charge_ai_calls()

    assert error.value.details == {"scope": "global_daily"}
    counters = [document async for document in db["rate_limits"].find({"_id": {"$regex": "^ai_"}})]
    assert len(counters) == 1
    assert counters[0]["count"] == 6


async def test_concurrent_counter_increments_are_not_lost(
    repos: Repositories, db: Database, make_settings: MakeSettings
) -> None:
    """Twenty requests race to create and increment the same counter document."""
    quotas = quota_service(repos, make_settings(global_daily_ai_call_limit=1_000))

    await asyncio.gather(*(quotas.charge_ai_calls() for _ in range(20)))

    counters = [document async for document in db["rate_limits"].find({})]
    assert len(counters) == 1
    assert counters[0]["count"] == 20
    assert await repos.counters.get(counters[0]["_id"]) == 20
    assert await repos.counters.get("no-such-counter") == 0

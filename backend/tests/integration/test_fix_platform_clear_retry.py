"""SEC-2: "Clear my data" can be repeated after a database error.

The session is revoked before anything is deleted. When a delete then failed,
the retry was answered 401 because the token was already revoked, and the
visitor's text stayed in the database until the 24-hour TTL.
"""

from collections.abc import Awaitable, Callable
from datetime import timedelta

import httpx
import pytest
from pymongo.errors import AutoReconnect

from app.db import Database
from app.repositories import Repositories
from app.repositories.jobs import JobRepository
from app.schemas.common import utc_now
from tests.conftest import SessionHandle
from tests.helpers import assert_error
from tests.integration.test_clear_data import EMPTY, SEEDED, owned_counts, seed_all_collections

CreateSession = Callable[[], Awaitable[SessionHandle]]


def fail_job_deletion_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """The third of the five delete passes loses its connection, once."""
    original = JobRepository.delete_for_owner
    failures = iter([AutoReconnect("connection reset by peer")])

    async def flaky(self: JobRepository, owner_id: str) -> int:
        for error in failures:
            raise error
        return await original(self, owner_id)

    monkeypatch.setattr(JobRepository, "delete_for_owner", flaky)


async def test_clear_can_be_retried_after_a_database_error(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    repos: Repositories,
    db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = await create_session()
    await seed_all_collections(repos, session.owner_id)
    fail_job_deletion_once(monkeypatch)

    failed = await client.delete("/api/session", headers=session.headers)

    error = assert_error(failed, 503, "database_unavailable")
    assert error["retryable"] is True
    left_behind = await owned_counts(db, session.owner_id)
    assert left_behind["sources"] == 1  # the pasted text is still stored

    retried = await client.delete("/api/session", headers=session.headers)

    assert retried.status_code == 200
    assert retried.json()["deleted_counts"] == {**left_behind, "generations": 0}
    assert await owned_counts(db, session.owner_id) == EMPTY


async def test_a_revoked_token_can_only_clear_its_own_data(
    client: httpx.AsyncClient, create_session: CreateSession, repos: Repositories, db: Database
) -> None:
    alice, bob = await create_session(), await create_session()
    await seed_all_collections(repos, alice.owner_id)
    await seed_all_collections(repos, bob.owner_id)
    assert (await client.delete("/api/session", headers=alice.headers)).status_code == 200

    again = await client.delete("/api/session", headers=alice.headers)

    assert again.status_code == 200
    assert again.json()["deleted_counts"] == EMPTY
    assert await owned_counts(db, bob.owner_id) == SEEDED
    # Every other route still refuses the revoked token.
    assert_error(await client.get("/api/session", headers=alice.headers), 401, "unauthorized")
    assert_error(await client.get("/api/profile", headers=alice.headers), 401, "unauthorized")
    assert_error(await client.get("/api/jobs", headers=alice.headers), 401, "unauthorized")


async def test_clear_is_refused_for_expired_and_unknown_tokens(
    client: httpx.AsyncClient, create_session: CreateSession, db: Database
) -> None:
    session = await create_session()
    await client.delete("/api/session", headers=session.headers)
    await db["sessions"].update_one(
        {"_id": session.session_id}, {"$set": {"expires_at": utc_now() - timedelta(seconds=1)}}
    )

    expired = await client.delete("/api/session", headers=session.headers)
    unknown = await client.delete("/api/session", headers={"Authorization": f"Bearer {'x' * 43}"})

    assert_error(expired, 401, "session_expired")
    assert_error(unknown, 401, "unauthorized")
    assert_error(await client.delete("/api/session"), 401, "unauthorized")

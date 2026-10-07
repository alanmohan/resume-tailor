"""Clearing a session ("Clear my data"): deletion from every owned collection,
isolation between sessions, and the post-write guard that stops in-flight work
from writing deleted data back."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import timedelta

import httpx
import pytest

from app.config import Settings
from app.db import OWNED_COLLECTIONS, Database
from app.errors import SessionExpired, Unauthorized
from app.providers.fake.provider import FakeProvider
from app.repositories import Repositories
from app.schemas.common import utc_now
from app.security import SessionContext
from app.services.sessions import SessionService
from tests.conftest import SessionHandle
from tests.factories import make_evidence, make_generation, make_job, make_profile, make_source
from tests.helpers import assert_error

CreateSession = Callable[[], Awaitable[SessionHandle]]


def context_of(session: SessionHandle) -> SessionContext:
    return SessionContext(
        owner_id=session.owner_id, session_id=session.session_id, expires_at=session.expires_at
    )


async def seed_all_collections(repos: Repositories, owner_id: str) -> None:
    """One profile, one source, two evidence records, one job and one draft."""
    profile = make_profile(owner_id)
    await repos.profiles.create(owner_id, profile)
    await repos.sources.insert(owner_id, make_source(owner_id, profile.profile_id))
    await repos.evidence.insert_many(
        owner_id,
        [make_evidence(owner_id, profile.profile_id), make_evidence(owner_id, profile.profile_id)],
    )
    await repos.jobs.insert(owner_id, make_job(owner_id))
    await repos.generations.insert(owner_id, make_generation(owner_id))


async def owned_counts(db: Database, owner_id: str) -> dict[str, int]:
    return {
        name: await db[name].count_documents({"owner_id": owner_id}) for name in OWNED_COLLECTIONS
    }


EMPTY = {"sources": 0, "profiles": 0, "evidence": 0, "jobs": 0, "generations": 0}
SEEDED = {"sources": 1, "profiles": 1, "evidence": 2, "jobs": 1, "generations": 1}


async def test_delete_removes_documents_from_every_owned_collection(
    client: httpx.AsyncClient, create_session: CreateSession, repos: Repositories, db: Database
) -> None:
    session = await create_session()
    await seed_all_collections(repos, session.owner_id)
    assert await owned_counts(db, session.owner_id) == SEEDED

    response = await client.delete("/api/session", headers=session.headers)

    assert response.status_code == 200
    assert response.json() == {"deleted": True, "deleted_counts": SEEDED}
    assert await owned_counts(db, session.owner_id) == EMPTY


async def test_delete_leaves_other_sessions_untouched(
    client: httpx.AsyncClient, create_session: CreateSession, repos: Repositories, db: Database
) -> None:
    alice = await create_session()
    bob = await create_session()
    await seed_all_collections(repos, alice.owner_id)
    await seed_all_collections(repos, bob.owner_id)

    await client.delete("/api/session", headers=alice.headers)

    assert await owned_counts(db, alice.owner_id) == EMPTY
    assert await owned_counts(db, bob.owner_id) == SEEDED
    assert (await client.get("/api/session", headers=bob.headers)).json()["has_profile"] is True


async def test_guard_passes_for_an_active_session(
    create_session: CreateSession, repos: Repositories, db: Database, settings: Settings
) -> None:
    session = await create_session()
    await seed_all_collections(repos, session.owner_id)

    await SessionService(repos, settings, "fake").guard_after_write(context_of(session))

    assert await owned_counts(db, session.owner_id) == SEEDED


async def test_write_after_deletion_is_cleaned_up_by_the_guard(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    repos: Repositories,
    db: Database,
    settings: Settings,
) -> None:
    session = await create_session()
    context = context_of(session)
    await client.delete("/api/session", headers=session.headers)

    # A request that was already past authentication finishes its write late.
    await repos.jobs.insert(context.owner_id, make_job(context.owner_id))
    await repos.evidence.insert_many(context.owner_id, [make_evidence(context.owner_id)])
    assert (await owned_counts(db, context.owner_id))["jobs"] == 1

    with pytest.raises(Unauthorized):
        await SessionService(repos, settings, "fake").guard_after_write(context)

    assert await owned_counts(db, context.owner_id) == EMPTY


async def test_guard_treats_an_expired_session_the_same_way(
    create_session: CreateSession, repos: Repositories, db: Database, settings: Settings
) -> None:
    session = await create_session()
    await seed_all_collections(repos, session.owner_id)
    await db["sessions"].update_one(
        {"_id": session.session_id}, {"$set": {"expires_at": utc_now() - timedelta(seconds=1)}}
    )

    with pytest.raises(SessionExpired):
        await SessionService(repos, settings, "fake").guard_after_write(context_of(session))

    assert await owned_counts(db, session.owner_id) == EMPTY


async def test_guard_handles_a_session_document_that_is_already_gone(
    create_session: CreateSession, repos: Repositories, db: Database, settings: Settings
) -> None:
    session = await create_session()
    await seed_all_collections(repos, session.owner_id)
    await db["sessions"].delete_one({"_id": session.session_id})  # as TTL cleanup would

    with pytest.raises(Unauthorized):
        await SessionService(repos, settings, "fake").guard_after_write(context_of(session))

    assert await owned_counts(db, session.owner_id) == EMPTY


async def test_clearing_data_during_a_slow_operation_leaves_nothing_behind(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    repos: Repositories,
    db: Database,
    settings: Settings,
    fake_provider: FakeProvider,
) -> None:
    """The full race: an operation is waiting on the provider when the visitor
    clears their data. The operation then stores its result, the guard notices
    the revocation, removes the result again and the request fails with 401."""
    session = await create_session()
    context = context_of(session)
    await seed_all_collections(repos, session.owner_id)
    service = SessionService(repos, settings, "fake")
    fake_provider.set_delay("embed", 0.2)

    async def slow_operation() -> None:
        # The shape every long-running feature operation follows.
        await fake_provider.embed(["Built Python data pipelines"])
        await repos.jobs.insert(context.owner_id, make_job(context.owner_id))
        await service.guard_after_write(context)

    operation = asyncio.create_task(slow_operation())
    await asyncio.sleep(0.05)  # the operation is now inside the provider call
    assert not operation.done()

    cleared = await client.delete("/api/session", headers=session.headers)
    assert cleared.status_code == 200
    assert cleared.json()["deleted_counts"] == SEEDED

    with pytest.raises(Unauthorized):
        await operation
    assert fake_provider.calls["embed"] == 1
    assert await owned_counts(db, session.owner_id) == EMPTY


async def test_deleted_data_cannot_be_read_back_with_the_old_token(
    client: httpx.AsyncClient, create_session: CreateSession, repos: Repositories
) -> None:
    session = await create_session()
    await seed_all_collections(repos, session.owner_id)
    await client.delete("/api/session", headers=session.headers)
    assert_error(await client.get("/api/session", headers=session.headers), 401, "unauthorized")

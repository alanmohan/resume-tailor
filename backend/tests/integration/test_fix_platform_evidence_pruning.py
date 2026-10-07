"""SEC-1: evidence of earlier profile versions is removed once nothing needs it.

Every confirm used to add a complete copy of the profile's evidence, vectors
included (about 20 kB each), so one visitor could fill the free database tier
by alternating a one-word edit and a confirm.
"""

from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from app.db import Database
from tests.conftest import SessionHandle
from tests.helpers_flow import analysed_job, cited_evidence_ids, generated
from tests.integration.test_profile_flow import (
    confirm,
    confirmed_profile,
    patch_body,
    simple_sources,
)

CreateSession = Callable[[], Awaitable[SessionHandle]]

JOB_SLUG = "close_fit"


async def versions_stored(db: Database, owner_id: str) -> dict[int, int]:
    """``{profile_version: number of evidence documents}`` for one owner."""
    counts: dict[int, int] = {}
    async for document in db["evidence"].find({"owner_id": owner_id}, {"profile_version": 1}):
        version = document["profile_version"]
        counts[version] = counts.get(version, 0) + 1
    return dict(sorted(counts.items()))


async def edit_and_confirm(
    client: httpx.AsyncClient, headers: dict[str, str], profile: dict[str, Any], minutes: int
) -> dict[str, Any]:
    """Change one bullet (a new profile version) and confirm it."""
    body = patch_body(profile)
    body["records"][0]["bullets"][1]["text"] = f"Cut report time from 3 hours to {minutes} minutes"
    edited = (await client.patch("/api/profile", json=body, headers=headers)).json()
    response = await confirm(client, headers, edited["version"])
    assert response.status_code == 200, response.text
    return response.json()


async def test_repeated_edit_and_confirm_keeps_only_the_current_version(
    client: httpx.AsyncClient, create_session: CreateSession, db: Database
) -> None:
    session = await create_session()
    profile = await confirmed_profile(client, session.headers, simple_sources())
    per_version = (await versions_stored(db, session.owner_id))[profile["version"]]

    for minutes in (15, 12, 10):
        profile = await edit_and_confirm(client, session.headers, profile, minutes)

    assert await versions_stored(db, session.owner_id) == {profile["version"]: per_version}


async def test_a_version_that_a_stored_draft_cites_is_kept(
    client: httpx.AsyncClient, create_session: CreateSession, db: Database
) -> None:
    session = await create_session()
    first = await confirmed_profile(client, session.headers, simple_sources())
    job = await analysed_job(client, session.headers, JOB_SLUG)
    draft = await generated(client, session.headers, job["job_id"], "pruning-key-0001")
    assert draft["profile_version"] == first["version"]

    second = await edit_and_confirm(client, session.headers, first, 15)
    third = await edit_and_confirm(client, session.headers, second, 12)

    # The draft's version and the current one remain; the one in between,
    # which no draft was written from, is gone.
    assert sorted(await versions_stored(db, session.owner_id)) == [
        first["version"],
        third["version"],
    ]
    # The now stale draft can still open its citations and be revalidated.
    for evidence_id in cited_evidence_ids(draft):
        opened = await client.get(f"/api/evidence/{evidence_id}", headers=session.headers)
        assert opened.status_code == 200, opened.text
    revalidated = await client.post(
        f"/api/generations/{draft['generation_id']}/validate", headers=session.headers
    )
    assert revalidated.status_code == 200, revalidated.text


async def test_another_visitors_evidence_is_never_pruned(
    client: httpx.AsyncClient, create_session: CreateSession, db: Database
) -> None:
    alice, bob = await create_session(), await create_session()
    bobs = await confirmed_profile(client, bob.headers, simple_sources())
    before = await versions_stored(db, bob.owner_id)
    profile = await confirmed_profile(client, alice.headers, simple_sources())

    # Alice's version number moves past Bob's; his version 1 must survive.
    await edit_and_confirm(client, alice.headers, profile, 15)

    assert bobs["version"] in before
    assert await versions_stored(db, bob.owner_id) == before

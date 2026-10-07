"""GET /api/evidence/{evidence_id}: what a citation shows, and who may see it."""

from collections.abc import Awaitable, Callable

import httpx

from app.db import Database
from tests.conftest import SessionHandle
from tests.helpers import assert_error
from tests.integration.test_profile_flow import (
    confirmed_profile,
    evidence_documents,
    patch_body,
    sample_sources,
    simple_sources,
)

CreateSession = Callable[[], Awaitable[SessionHandle]]


def contains_key(value: object, key: str) -> bool:
    """True if ``key`` occurs anywhere in a nested JSON value."""
    if isinstance(value, dict):
        return key in value or any(contains_key(item, key) for item in value.values())
    if isinstance(value, list):
        return any(contains_key(item, key) for item in value)
    return False


async def test_evidence_shows_excerpt_source_span_parent_and_provenance(
    client: httpx.AsyncClient, create_session: CreateSession, db: Database
) -> None:
    session = await create_session()
    profile = await confirmed_profile(client, session.headers, sample_sources())
    stored = await evidence_documents(db, session.owner_id, profile["version"])
    document = next(item for item in stored if "1.2 million log lines" in item["text"])

    response = await client.get(f"/api/evidence/{document['_id']}", headers=session.headers)

    assert response.status_code == 200
    evidence = response.json()
    assert set(evidence) == {
        "evidence_id",
        "excerpt",
        "text",
        "category",
        "source",
        "provenance",
        "parent",
        "tags",
        "profile_version",
    }
    resume = sample_sources()[0]["text"]
    source = evidence["source"]
    assert evidence["evidence_id"] == document["_id"]
    assert evidence["excerpt"] == (
        "Wrote Python scripts that parse robot telemetry logs and flag sensor dropouts, "
        "processing 1.2 million log lines per day"
    )
    assert resume[source["start"] : source["end"]] == evidence["excerpt"]
    assert (source["label"], source["source_type"]) == ("Resume", "resume")
    assert source["source_id"] == profile["sources"][0]["source_id"]
    assert evidence["text"].startswith("[Software Engineering Intern at Harborline Robotics] ")
    assert evidence["category"] == "employment"
    assert evidence["provenance"] == "extracted"
    assert evidence["tags"] == ["python"]
    assert evidence["profile_version"] == profile["version"]
    role = next(r for r in profile["records"] if r["organization"] == "Harborline Robotics")
    assert evidence["parent"] == {
        "record_id": role["record_id"],
        "category": "employment",
        "title": "Software Engineering Intern",
        "organization": "Harborline Robotics",
    }


async def test_embedding_vectors_are_never_returned(
    client: httpx.AsyncClient, create_session: CreateSession, db: Database
) -> None:
    session = await create_session()
    profile = await confirmed_profile(client, session.headers, simple_sources())
    stored = await evidence_documents(db, session.owner_id, profile["version"])
    assert all(len(document["embedding"]) == 256 for document in stored)

    for document in stored:
        evidence = (
            await client.get(f"/api/evidence/{document['_id']}", headers=session.headers)
        ).json()
        for hidden in ("embedding", "embedding_model", "content_hash", "owner_id"):
            assert not contains_key(evidence, hidden)
    # The profile and session responses do not carry vectors either.
    assert not contains_key(profile, "embedding")


async def test_statement_edited_by_the_user_is_cited_as_their_own(
    client: httpx.AsyncClient, create_session: CreateSession, db: Database
) -> None:
    session = await create_session()
    profile = await confirmed_profile(client, session.headers, simple_sources())
    body = patch_body(profile)
    body["records"][0]["bullets"][0]["text"] = "Built Python pipelines for 20 analysts"
    edited = (await client.patch("/api/profile", json=body, headers=session.headers)).json()
    await client.post(
        "/api/profile/confirm",
        json={"expected_version": edited["version"]},
        headers=session.headers,
    )
    bullet_id = edited["records"][0]["bullets"][0]["bullet_id"]
    stored = await evidence_documents(db, session.owner_id, edited["version"])
    document = next(item for item in stored if item["bullet_id"] == bullet_id)

    evidence = (
        await client.get(f"/api/evidence/{document['_id']}", headers=session.headers)
    ).json()

    assert evidence["provenance"] == "user_edited"
    assert evidence["excerpt"] == "Built Python pipelines for 20 analysts"
    assert evidence["source"] == {
        "source_id": None,
        "label": "Edited during profile review",
        "source_type": "user",
        "start": None,
        "end": None,
    }
    assert evidence["parent"]["title"] == "Data Engineer"


async def test_evidence_of_an_older_profile_version_can_still_be_opened(
    client: httpx.AsyncClient, create_session: CreateSession, db: Database
) -> None:
    session = await create_session()
    profile = await confirmed_profile(client, session.headers, simple_sources())
    old = (await evidence_documents(db, session.owner_id, profile["version"]))[1]
    body = patch_body(profile)
    body["records"][0]["bullets"][0]["text"] = "Rewrote everything"
    await client.patch("/api/profile", json=body, headers=session.headers)

    response = await client.get(f"/api/evidence/{old['_id']}", headers=session.headers)

    assert response.status_code == 200
    assert response.json()["excerpt"] == "Built Python pipelines for 12 analysts"
    assert response.json()["profile_version"] == profile["version"]


async def test_evidence_of_another_session_looks_exactly_like_a_missing_one(
    client: httpx.AsyncClient, create_session: CreateSession, db: Database
) -> None:
    alice, bob = await create_session(), await create_session()
    profile = await confirmed_profile(client, alice.headers, simple_sources())
    await confirmed_profile(client, bob.headers, sample_sources())
    evidence_id = (await evidence_documents(db, alice.owner_id, profile["version"]))[0]["_id"]

    foreign = await client.get(f"/api/evidence/{evidence_id}", headers=bob.headers)
    missing = await client.get(f"/api/evidence/{'0' * 32}", headers=bob.headers)

    assert (
        assert_error(foreign, 404, "not_found")["message"]
        == (assert_error(missing, 404, "not_found")["message"])
    )
    assert "Riley" not in foreign.text and "Northwind" not in foreign.text
    assert (
        await client.get(f"/api/evidence/{evidence_id}", headers=alice.headers)
    ).status_code == 200


async def test_evidence_is_gone_after_the_session_is_cleared(
    client: httpx.AsyncClient, create_session: CreateSession, db: Database
) -> None:
    session = await create_session()
    profile = await confirmed_profile(client, session.headers, simple_sources())
    evidence_id = (await evidence_documents(db, session.owner_id, profile["version"]))[0]["_id"]

    cleared = await client.delete("/api/session", headers=session.headers)

    assert cleared.json()["deleted_counts"] == {
        "sources": 1,
        "profiles": 1,
        "evidence": 4,
        "jobs": 0,
        "generations": 0,
    }
    assert_error(
        await client.get(f"/api/evidence/{evidence_id}", headers=session.headers),
        401,
        "unauthorized",
    )
    assert await db["evidence"].count_documents({"owner_id": session.owner_id}) == 0

"""POST /api/profile/confirm: conflicts and versions, evidence and embeddings,
the embedding cache, recoverable failures, limits and concurrency."""

import asyncio
from collections.abc import Awaitable, Callable

import httpx
import pytest
from fastapi import FastAPI

from app.config import Settings
from app.db import Database
from app.providers.base import ProviderRateLimited, ProviderTimeout
from app.providers.fake.embeddings import fake_embedding
from app.providers.fake.provider import FakeProvider
from app.repositories import Repositories
from app.repositories.evidence import EvidenceRepository
from app.services import indexing
from tests.conftest import SessionHandle, client_for
from tests.factories import make_evidence, make_profile
from tests.helpers import assert_error
from tests.integration.test_profile_flow import (
    confirm,
    confirmed_profile,
    evidence_documents,
    ingest,
    patch_body,
    resolve_conflicts,
    simple_sources,
    source,
)

CreateSession = Callable[[], Awaitable[SessionHandle]]
MakeApp = Callable[[Settings], Awaitable[FastAPI]]
MakeSettings = Callable[..., Settings]


def record_embedded_texts(
    provider: FakeProvider, monkeypatch: pytest.MonkeyPatch
) -> list[list[str]]:
    """Spy on provider.embed: returns a list that receives the texts of every call."""
    batches: list[list[str]] = []
    original = provider.embed

    async def spy(texts: list[str]) -> tuple[list[list[float]], object]:
        batches.append(list(texts))
        return await original(texts)

    monkeypatch.setattr(provider, "embed", spy)
    return batches


async def current_profile(client: httpx.AsyncClient, headers: dict[str, str]) -> dict:
    return (await client.get("/api/profile", headers=headers)).json()


# ---- Preconditions -----------------------------------------------------------------


async def test_confirm_is_rejected_until_every_conflict_is_resolved(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    db: Database,
    fake_provider: FakeProvider,
) -> None:
    session = await create_session()
    profile = await ingest(client, session.headers)

    error = assert_error(
        await confirm(client, session.headers, profile["version"]), 409, "unresolved_conflicts"
    )

    assert error["details"] == {"unresolved_conflict_count": 1}
    assert fake_provider.calls["embed"] == 0
    assert await db["evidence"].count_documents({"owner_id": session.owner_id}) == 0
    assert await current_profile(client, session.headers) == profile
    # A rejected confirmation is not counted against the quota.
    assert (await db["sessions"].find_one({"_id": session.session_id}))["quota"]["confirm"] == 0

    resolved = await resolve_conflicts(client, session.headers, profile)
    assert (await confirm(client, session.headers, resolved["version"])).status_code == 200


async def test_confirm_with_a_stale_version_is_a_conflict(
    client: httpx.AsyncClient, auth_headers: dict[str, str], fake_provider: FakeProvider
) -> None:
    profile = await ingest(client, auth_headers, simple_sources())

    error = assert_error(
        await confirm(client, auth_headers, profile["version"] + 1), 409, "version_conflict"
    )

    assert error["details"] == {"current_version": profile["version"]}
    assert fake_provider.calls["embed"] == 0
    assert (await current_profile(client, auth_headers))["status"] == "draft"


async def test_profile_without_content_cannot_be_confirmed(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    profile = await ingest(client, auth_headers, [source("Notes", "notes", "Nothing here yet.")])
    assert profile["records"] == []

    error = assert_error(
        await confirm(client, auth_headers, profile["version"]), 422, "validation_error"
    )
    assert error["field_errors"][0]["field"] == "records"


async def test_profile_with_too_many_chunks_is_rejected_with_the_limit(
    make_app: MakeApp, make_settings: MakeSettings
) -> None:
    application = await make_app(make_settings(max_evidence_chunks=3))
    async with client_for(application) as client:
        token = (await client.post("/api/sessions")).json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        profile = await ingest(client, headers, simple_sources())

        error = assert_error(
            await confirm(client, headers, profile["version"]), 422, "too_many_chunks"
        )

        assert error["details"] == {"chunks": 4, "limit": 3}
        assert "4 evidence chunks; the limit is 3" in error["message"]
        assert "Remove or shorten" in error["message"]
        assert application.state.provider.calls["embed"] == 0
        assert await application.state.mongo.db["evidence"].count_documents({}) == 0
        assert (await current_profile(client, headers))["index_state"] == "not_indexed"

        # Reducing the input, as the message asks, makes the same profile confirmable.
        body = patch_body(profile)
        del body["records"][0]["bullets"][1]
        smaller = (await client.patch("/api/profile", json=body, headers=headers)).json()
        assert (await confirm(client, headers, smaller["version"])).status_code == 200


# ---- Success -----------------------------------------------------------------------


async def test_confirm_stores_embedded_evidence_for_exactly_this_version(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    db: Database,
    fake_provider: FakeProvider,
) -> None:
    session = await create_session()
    profile = await ingest(client, session.headers, simple_sources())

    response = await confirm(client, session.headers, profile["version"])

    assert response.status_code == 200
    confirmed = response.json()
    assert confirmed["version"] == profile["version"]
    assert (confirmed["status"], confirmed["index_state"]) == ("confirmed", "indexed")
    assert confirmed["indexed_version"] == confirmed["version"]
    assert confirmed["index_progress"] == {"total": 4, "embedded": 4}
    assert confirmed["records"] == profile["records"]

    stored = await evidence_documents(db, session.owner_id, confirmed["version"])
    assert [document["text"] for document in stored] == [
        "[Data Engineer at Northwind Labs] Jan 2020 - Mar 2021",
        "[Data Engineer at Northwind Labs] Built Python pipelines for 12 analysts",
        "[Data Engineer at Northwind Labs] Cut report time from 3 hours to 20 minutes",
        "[Skills (Languages)] Python, SQL",
    ]
    for document in stored:
        assert document["embedding_status"] == "embedded"
        assert document["embedding"] == fake_embedding(document["text"])
        assert document["embedding_model"] == "fake-embedding-256"
        assert document["embedding_dimension"] == 256
        assert document["profile_id"] == confirmed["profile_id"]
        assert document["expires_at"] == session.expires_at
    assert fake_provider.calls["embed"] == 1


async def test_confirming_an_indexed_version_again_changes_and_costs_nothing(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    db: Database,
    fake_provider: FakeProvider,
) -> None:
    session = await create_session()
    confirmed = await confirmed_profile(client, session.headers, simple_sources())
    before = await evidence_documents(db, session.owner_id, confirmed["version"])

    again = await confirm(client, session.headers, confirmed["version"])

    assert again.status_code == 200
    assert again.json()["index_state"] == "indexed"
    assert fake_provider.calls["embed"] == 1
    # The same documents, not new copies: citations in existing drafts stay valid.
    assert await evidence_documents(db, session.owner_id, confirmed["version"]) == before


async def test_only_changed_statements_are_embedded_after_an_edit(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    db: Database,
    fake_provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = await create_session()
    confirmed = await confirmed_profile(client, session.headers, simple_sources())
    old_version = confirmed["version"]
    old = await evidence_documents(db, session.owner_id, old_version)
    body = patch_body(confirmed)
    body["records"][0]["bullets"][1]["text"] = "Cut report time from 3 hours to 15 minutes"
    edited = (await client.patch("/api/profile", json=body, headers=session.headers)).json()
    embedded = record_embedded_texts(fake_provider, monkeypatch)

    response = await confirm(client, session.headers, edited["version"])

    assert response.status_code == 200
    assert response.json()["indexed_version"] == edited["version"] == old_version + 1
    # Embedding cache: three of the four statements are unchanged and reuse
    # their vectors; only the edited one is sent to the provider.
    assert embedded == [
        ["[Data Engineer at Northwind Labs] Cut report time from 3 hours to 15 minutes"]
    ]
    new = await evidence_documents(db, session.owner_id, edited["version"])
    assert len(new) == 4
    assert all(document["embedding"] == fake_embedding(document["text"]) for document in new)
    assert [document["provenance"] for document in new] == [
        "extracted",
        "extracted",
        "user_edited",
        "extracted",
    ]
    # Versions are never mixed: the new version has its own documents with
    # different IDs. The old version's evidence is removed once the new one is
    # indexed, because no draft was written from it (SEC-1).
    assert len(old) == 4
    assert not {document["_id"] for document in old} & {document["_id"] for document in new}
    assert await evidence_documents(db, session.owner_id, old_version) == []


async def test_vectors_of_another_embedding_model_are_never_reused(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    db: Database,
    fake_provider: FakeProvider,
) -> None:
    session = await create_session()
    confirmed = await confirmed_profile(client, session.headers, simple_sources())
    ids_before = [
        document["_id"]
        for document in await evidence_documents(db, session.owner_id, confirmed["version"])
    ]
    # As if the server had been reconfigured to another embedding model.
    await db["evidence"].update_many(
        {"owner_id": session.owner_id},
        {"$set": {"embedding_model": "older-model", "embedding": [0.5, 0.5]}},
    )

    response = await confirm(client, session.headers, confirmed["version"])

    assert response.status_code == 200
    assert fake_provider.calls["embed"] == 2
    rebuilt = await evidence_documents(db, session.owner_id, confirmed["version"])
    assert [document["_id"] for document in rebuilt] == ids_before
    for document in rebuilt:
        assert document["embedding_model"] == "fake-embedding-256"
        assert document["embedding"] == fake_embedding(document["text"])


# ---- Failure and recovery ----------------------------------------------------------


async def test_embedding_failure_is_recorded_and_a_retry_resumes(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    db: Database,
    fake_provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = await create_session()
    profile = await resolve_conflicts(
        client, session.headers, await ingest(client, session.headers)
    )
    monkeypatch.setattr(indexing, "EMBED_BATCH_SIZE", 10)
    calls: list[list[str]] = []
    real_embed = fake_provider.embed

    async def embed_failing_on_the_third_call(texts: list[str]) -> tuple[list[list[float]], object]:
        calls.append(list(texts))
        if len(calls) == 3:
            raise ProviderTimeout("The AI provider took too long.")
        return await real_embed(texts)

    monkeypatch.setattr(fake_provider, "embed", embed_failing_on_the_third_call)

    error = assert_error(
        await confirm(client, session.headers, profile["version"]), 504, "provider_timeout"
    )

    assert error["retryable"] is True
    failed = await current_profile(client, session.headers)
    total = failed["index_progress"]["total"]
    assert total > 40
    assert (failed["status"], failed["index_state"]) == ("draft", "failed")
    assert failed["indexed_version"] is None
    assert failed["index_progress"] == {"total": total, "embedded": 20}
    # The message is generic: it says what to do and quotes no profile text.
    assert failed["index_error"] == indexing.INDEX_FAILED_MESSAGE
    # The reviewed profile itself is untouched.
    assert failed["records"] == profile["records"]
    stored = await evidence_documents(db, session.owner_id, profile["version"])
    statuses = [document["embedding_status"] for document in stored]
    assert len(stored) == total
    assert statuses.count("embedded") == 20
    assert statuses.count("failed") == 10
    assert statuses.count("pending") == total - 30

    retried = await confirm(client, session.headers, profile["version"])

    assert retried.status_code == 200
    done = retried.json()
    assert (done["status"], done["index_state"]) == ("confirmed", "indexed")
    assert done["index_progress"] == {"total": total, "embedded": total}
    assert done["index_error"] is None
    # The retry embedded only what was still missing.
    embedded_before = {
        document["text"] for document in stored if document["embedding_status"] == "embedded"
    }
    retried_texts = [text for batch in calls[3:] for text in batch]
    assert len(retried_texts) == total - 20
    assert not embedded_before & set(retried_texts)
    after = await evidence_documents(db, session.owner_id, profile["version"])
    assert all(document["embedding_status"] == "embedded" for document in after)
    assert [document["_id"] for document in after] == [document["_id"] for document in stored]


async def test_rate_limited_embedding_maps_to_503_and_keeps_the_profile(
    client: httpx.AsyncClient, auth_headers: dict[str, str], fake_provider: FakeProvider
) -> None:
    profile = await ingest(client, auth_headers, simple_sources())
    fake_provider.fail_next("embed", ProviderRateLimited("The AI provider is rate limiting."))

    error = assert_error(
        await confirm(client, auth_headers, profile["version"]), 503, "provider_rate_limited"
    )

    assert error["retryable"] is True
    failed = await current_profile(client, auth_headers)
    assert (failed["status"], failed["index_state"]) == ("draft", "failed")
    assert failed["index_progress"] == {"total": 4, "embedded": 0}
    assert failed["records"] == profile["records"]
    assert (await confirm(client, auth_headers, profile["version"])).status_code == 200


async def test_profile_is_not_marked_indexed_unless_every_vector_is_stored(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = await create_session()
    profile = await ingest(client, session.headers, simple_sources())

    async def lose_the_vectors(self: EvidenceRepository, owner_id: str, vectors: dict) -> int:
        return 0

    with monkeypatch.context() as patched:
        patched.setattr(EvidenceRepository, "set_embeddings", lose_the_vectors)
        refused = await confirm(client, session.headers, profile["version"])

    # The provider answered, but the final check reads the database: evidence
    # without vectors must never count as an indexed profile.
    assert_error(refused, 409, "profile_not_indexed")
    failed = await current_profile(client, session.headers)
    assert (failed["status"], failed["index_state"]) == ("draft", "failed")
    assert failed["indexed_version"] is None
    assert failed["index_progress"] == {"total": 4, "embedded": 0}

    recovered = await confirm(client, session.headers, profile["version"])
    assert recovered.status_code == 200
    assert recovered.json()["index_state"] == "indexed"


async def test_confirm_quota_is_enforced_per_session(
    make_app: MakeApp, make_settings: MakeSettings
) -> None:
    application = await make_app(make_settings(quota_confirm=1))
    async with client_for(application) as client:
        token = (await client.post("/api/sessions")).json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        confirmed = await confirmed_profile(client, headers, simple_sources())

        error = assert_error(
            await confirm(client, headers, confirmed["version"]), 429, "quota_exceeded"
        )
        assert error["details"] == {"operation": "confirm", "limit": 1}
        assert (await current_profile(client, headers))["index_state"] == "indexed"


# ---- Concurrency -------------------------------------------------------------------


async def test_two_confirm_requests_at_once_converge_on_one_set_of_evidence(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    db: Database,
    fake_provider: FakeProvider,
) -> None:
    session = await create_session()
    profile = await ingest(client, session.headers, simple_sources())
    fake_provider.set_delay("embed", 0.05)

    first, second = await asyncio.gather(
        confirm(client, session.headers, profile["version"]),
        confirm(client, session.headers, profile["version"]),
    )

    assert (first.status_code, second.status_code) == (200, 200)
    final = await current_profile(client, session.headers)
    assert (final["status"], final["index_state"]) == ("confirmed", "indexed")
    assert final["index_progress"] == {"total": 4, "embedded": 4}
    stored = await evidence_documents(db, session.owner_id, profile["version"])
    assert len(stored) == 4
    assert all(document["embedding"] == fake_embedding(document["text"]) for document in stored)


async def test_documents_stored_first_by_a_concurrent_confirm_are_not_an_error(
    repos: Repositories, db: Database, fake_provider: FakeProvider
) -> None:
    """The exact interleaving behind the test above, made deterministic: the
    other request has already stored one of the documents this one plans to
    insert, so the bulk insert stops at a duplicate ID."""
    profile = make_profile("owner-1")
    documents = [
        make_evidence("owner-1", profile.profile_id, evidence_id=f"evidence-{number}")
        for number in range(4)
    ]
    await repos.evidence.insert_many("owner-1", [documents[1]])
    run = indexing._IndexRun(profile, documents, repos, fake_provider, quotas=None)

    await run._insert(documents)

    stored = await evidence_documents(db, "owner-1", profile.version)
    assert sorted(document["_id"] for document in stored) == [
        "evidence-0",
        "evidence-1",
        "evidence-2",
        "evidence-3",
    ]


async def test_edit_during_indexing_prevents_the_old_version_from_being_marked_indexed(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    fake_provider: FakeProvider,
) -> None:
    session = await create_session()
    profile = await ingest(client, session.headers, simple_sources())
    fake_provider.set_delay("embed", 0.25)

    confirming = asyncio.create_task(confirm(client, session.headers, profile["version"]))
    await asyncio.sleep(0.05)  # the request is now waiting for the embeddings
    body = patch_body(profile)
    body["records"][0]["bullets"][0]["text"] = "Built Python pipelines for 20 analysts"
    edited = (await client.patch("/api/profile", json=body, headers=session.headers)).json()

    assert_error(await confirming, 409, "version_conflict")
    final = await current_profile(client, session.headers)
    assert final["version"] == edited["version"]
    assert (final["status"], final["index_state"]) == ("draft", "not_indexed")
    assert final["indexed_version"] is None


async def test_clearing_data_while_indexing_leaves_no_evidence_behind(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    db: Database,
    fake_provider: FakeProvider,
) -> None:
    session = await create_session()
    profile = await ingest(client, session.headers, simple_sources())
    fake_provider.set_delay("embed", 0.25)

    confirming = asyncio.create_task(confirm(client, session.headers, profile["version"]))
    await asyncio.sleep(0.05)
    assert not confirming.done()
    assert (await client.delete("/api/session", headers=session.headers)).status_code == 200

    assert_error(await confirming, 401, "unauthorized")
    for name in ("sources", "profiles", "evidence"):
        assert await db[name].count_documents({"owner_id": session.owner_id}) == 0

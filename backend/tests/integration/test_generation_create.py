"""POST /api/generations with the deterministic fake provider: the happy path,
idempotency, preconditions, quota, empty evidence and stale drafts."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from app.config import Settings
from app.db import Database
from app.providers.fake.embeddings import fake_embedding
from app.providers.fake.provider import FakeProvider
from app.repositories import Repositories
from app.services.generation import NO_EVIDENCE_WARNING
from tests.conftest import SessionHandle, client_for
from tests.factories import make_evidence, make_profile
from tests.helpers import assert_error, parse_iso_z
from tests.helpers_generation import (
    CONTACT,
    all_claims,
    all_text,
    entry_for,
    evidence_for_profile,
    post_generation,
    sample_records,
    seed_job,
    seed_profile,
    wait_for_call,
    with_key,
)

CreateSession = Callable[[], Awaitable[SessionHandle]]


async def quota_used(db: Database, session: SessionHandle, operation: str) -> int:
    stored = await db["sessions"].find_one({"_id": session.session_id})
    return stored["quota"].get(operation, 0)


# ---- happy path --------------------------------------------------------------------


async def test_generation_returns_a_grounded_draft(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    repos: Repositories,
    fake_provider: FakeProvider,
) -> None:
    session = await create_session()
    seeded = await seed_profile(repos, session)
    job = await seed_job(repos, session)

    response = await post_generation(client, session, job)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "completed" and body["error"] is None
    assert (body["job_id"], body["job_version"], body["job_title"], body["company"]) == (
        job.job_id,
        1,
        "Platform Engineer",
        "Globex",
    )
    assert (body["profile_id"], body["profile_version"]) == (seeded.profile.profile_id, 1)
    assert (body["provider_mode"], body["model"]) == ("fake", "fake-llm-1")
    assert (body["stale"], body["stale_reasons"], body["revision"]) == (False, [], 1)
    assert parse_iso_z(body["expires_at"]) == session.expires_at

    # Mandatory metadata is the confirmed profile, verbatim.
    assert body["resume"]["contact"] == CONTACT.model_dump()
    role = entry_for(body, "experience", "role-quill")
    assert (role["heading"], role["subheading"], role["location"], role["date_range"]) == (
        "Software Engineer",
        "Quillfeather Software",
        "Pittsburgh, PA",
        "Jul 2022 - Jul 2024",
    )
    degree = entry_for(body, "education", "edu-fairhaven")
    assert (degree["heading"], degree["subheading"]) == (
        "B.S. in Computer Science",
        "Fairhaven Institute of Technology",
    )
    assert (
        entry_for(body, "certifications", "cert-cloud")["heading"]
        == "Cloud Practitioner Certificate"
    )

    # Every statement is validated and cites only this owner's evidence.
    owned_ids = {evidence.evidence_id for evidence in seeded.evidence}
    retrieved = set(body["retrieved_evidence_ids"])
    assert retrieved and retrieved <= owned_ids
    claims = all_claims(body)
    assert role["bullets"] and body["resume"]["summary"] and body["resume"]["skills"]
    for claim in claims:
        assert set(claim["evidence_ids"]) <= owned_ids
        assert claim["validation_status"] in {"supported", "not_applicable"}
        assert claim["user_edited"] is False
        if claim["validation_status"] == "supported":
            assert claim["evidence_ids"], claim["text"]
    for entry in body["resume"]["experience"]:
        for bullet in entry["bullets"]:
            assert set(bullet["evidence_ids"]) <= retrieved
    letter = body["cover_letter"]["paragraphs"]
    assert letter[0]["validation_status"] == "not_applicable"
    assert letter[-1]["validation_status"] == "not_applicable"
    assert body["validation"]["state"] == "validated"
    assert body["validation"]["validated_at"] is not None
    assert (body["omitted_claims"], body["warnings"]) == ([], [])

    # The profile mentions Docker and Python but not Kubernetes.
    assert "Kubernetes" not in all_text(body)
    coverage = {item["requirement_id"]: item for item in body["coverage"]}
    assert coverage["req-python"]["status"] == "supported"
    assert coverage["req-docker"]["status"] == "supported"
    evidence_text = {evidence.evidence_id: evidence.text for evidence in seeded.evidence}
    assert coverage["req-docker"]["evidence_ids"]
    for evidence_id in coverage["req-docker"]["evidence_ids"]:
        assert "Docker" in evidence_text[evidence_id]
    assert coverage["req-k8s"]["status"] == "missing"
    assert "found in the supplied profile" in coverage["req-k8s"]["rationale"]
    assert body["coverage_summary"] == {
        "supported": 2,
        "partial": 0,
        "missing": 1,
        "uncertain": 0,
        "assessed": 3,
        "percent": 66.7,
    }

    # One embedding call for all queries and one generation call.
    assert (fake_provider.calls["embed"], fake_provider.calls["generate_documents"]) == (1, 1)
    assert body["usage"]["provider_calls"] == 2
    assert body["usage"]["embedding_tokens"] > 0 and body["usage"]["output_tokens"] > 0


async def test_generation_is_stored_with_owner_expiry_and_audit_fields(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    repos: Repositories,
    db: Database,
) -> None:
    session = await create_session()
    await seed_profile(repos, session)
    job = await seed_job(repos, session)
    body = (await post_generation(client, session, job, key="audit-key-001")).json()

    stored = await db["generations"].find_one({"_id": body["generation_id"]})
    assert stored["owner_id"] == session.owner_id
    assert stored["expires_at"].replace(tzinfo=session.expires_at.tzinfo) == session.expires_at
    assert stored["idempotency_key"] == "audit-key-001"
    assert (stored["status"], stored["attempt"]) == ("completed", 1)
    assert (stored["profile_version"], stored["job_version"]) == (1, 1)
    assert stored["retrieved_evidence_ids"] == body["retrieved_evidence_ids"]
    assert stored["usage"]["provider_calls"] == 2

    fetched = await client.get(f"/api/generations/{body['generation_id']}", headers=session.headers)
    assert fetched.status_code == 200
    assert fetched.json() == body
    listed = await client.get("/api/generations", headers=session.headers)
    assert listed.json() == {
        "generations": [
            {
                "generation_id": body["generation_id"],
                "job_id": job.job_id,
                "job_title": "Platform Engineer",
                "company": "Globex",
                "status": "completed",
                "stale": False,
                "created_at": body["created_at"],
            }
        ]
    }


async def test_cited_evidence_can_be_loaded_by_its_owner_only(
    client: httpx.AsyncClient, create_session: CreateSession, repos: Repositories
) -> None:
    alice = await create_session()
    bob = await create_session()
    await seed_profile(repos, alice)
    job = await seed_job(repos, alice)
    body = (await post_generation(client, alice, job)).json()

    cited = {evidence_id for claim in all_claims(body) for evidence_id in claim["evidence_ids"]}
    assert cited
    for evidence_id in cited:
        assert await repos.evidence.get(alice.owner_id, evidence_id) is not None
        assert await repos.evidence.get(bob.owner_id, evidence_id) is None


# ---- idempotency -------------------------------------------------------------------


@pytest.mark.parametrize(
    "headers", [{}, {"Idempotency-Key": "short"}, {"Idempotency-Key": "a b c d e f"}]
)
async def test_missing_or_malformed_idempotency_key_is_rejected(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    repos: Repositories,
    db: Database,
    fake_provider: FakeProvider,
    headers: dict[str, str],
) -> None:
    session = await create_session()
    await seed_profile(repos, session)
    job = await seed_job(repos, session)

    response = await client.post(
        "/api/generations", json={"job_id": job.job_id}, headers={**session.headers, **headers}
    )

    assert_error(response, 400, "idempotency_key_required")
    assert fake_provider.calls["generate_documents"] == 0
    assert await db["generations"].count_documents({}) == 0
    assert await quota_used(db, session, "generation") == 0


async def test_repeating_a_key_returns_the_stored_draft_without_a_second_generation(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    repos: Repositories,
    db: Database,
    fake_provider: FakeProvider,
) -> None:
    session = await create_session()
    await seed_profile(repos, session)
    job = await seed_job(repos, session)

    first = await post_generation(client, session, job, key="same-key-123")
    second = await post_generation(client, session, job, key="same-key-123")
    third = await post_generation(client, session, job, key="same-key-123")

    assert (first.status_code, second.status_code, third.status_code) == (201, 200, 200)
    assert second.json() == first.json() == third.json()
    assert fake_provider.calls["generate_documents"] == 1
    assert fake_provider.calls["embed"] == 1
    assert await db["generations"].count_documents({"owner_id": session.owner_id}) == 1
    # A replay is free: only the first request used quota.
    assert await quota_used(db, session, "generation") == 1


async def test_replay_still_works_after_the_profile_changed(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    repos: Repositories,
    fake_provider: FakeProvider,
) -> None:
    session = await create_session()
    seeded = await seed_profile(repos, session)
    job = await seed_job(repos, session)
    first = await post_generation(client, session, job, key="replay-key-1")

    edited = seeded.profile.model_copy(update={"version": 2, "status": "draft"})
    assert await repos.profiles.replace(session.owner_id, edited, expected_version=1)
    replay = await post_generation(client, session, job, key="replay-key-1")

    assert replay.status_code == 200
    assert replay.json()["generation_id"] == first.json()["generation_id"]
    assert replay.json()["stale_reasons"] == ["profile_changed"]
    assert fake_provider.calls["generate_documents"] == 1
    # A new key, however, is refused until the profile is confirmed again.
    fresh = await post_generation(client, session, job, key="replay-key-2")
    assert_error(fresh, 409, "profile_not_confirmed")


async def test_a_key_cannot_be_reused_for_a_different_job(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    repos: Repositories,
    fake_provider: FakeProvider,
) -> None:
    session = await create_session()
    await seed_profile(repos, session)
    first_job = await seed_job(repos, session)
    other_job = await seed_job(repos, session, title="Data Engineer")
    await post_generation(client, session, first_job, key="one-key-only")

    response = await post_generation(client, session, other_job, key="one-key-only")

    error = assert_error(response, 422, "validation_error")
    assert error["field_errors"][0]["field"] == "job_id"
    assert fake_provider.calls["generate_documents"] == 1


async def test_concurrent_duplicates_run_one_generation(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    repos: Repositories,
    fake_provider: FakeProvider,
) -> None:
    session = await create_session()
    await seed_profile(repos, session)
    job = await seed_job(repos, session)
    fake_provider.set_delay("generate_documents", 0.5)

    responses = await asyncio.gather(
        *(post_generation(client, session, job, key="race-key-001") for _ in range(4))
    )

    # Exactly one request generated. Every other one was told the generation is
    # in progress (or, had it arrived after the end, would get the same draft).
    assert fake_provider.calls["generate_documents"] == 1
    created = [response.json() for response in responses if response.status_code == 201]
    assert len(created) == 1
    for response in responses:
        if response.status_code == 409:
            error = assert_error(response, 409, "generation_in_progress")
            assert error["details"] == {"generation_id": created[0]["generation_id"]}
        else:
            assert response.status_code in (200, 201)
            assert response.json() == created[0]
    assert sum(response.status_code == 409 for response in responses) >= 1
    # The client that got 409 polls the draft by ID and finds the same result.
    polled = await client.get(
        f"/api/generations/{created[0]['generation_id']}", headers=session.headers
    )
    assert polled.json() == created[0]


async def test_duplicate_while_running_gets_409_and_the_draft_once_finished(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    repos: Repositories,
    fake_provider: FakeProvider,
) -> None:
    session = await create_session()
    await seed_profile(repos, session)
    job = await seed_job(repos, session)
    fake_provider.set_delay("generate_documents", 0.5)

    running = asyncio.create_task(post_generation(client, session, job, key="slow-key-001"))
    await wait_for_call(fake_provider, "generate_documents")
    during = await post_generation(client, session, job, key="slow-key-001")
    error = assert_error(during, 409, "generation_in_progress")
    in_flight = await client.get(
        f"/api/generations/{error['details']['generation_id']}", headers=session.headers
    )
    assert (in_flight.json()["status"], in_flight.json()["resume"]) == ("running", None)

    first = await running
    after = await post_generation(client, session, job, key="slow-key-001")

    assert (first.status_code, after.status_code) == (201, 200)
    assert after.json() == first.json()
    assert error["details"]["generation_id"] == first.json()["generation_id"]
    assert fake_provider.calls["generate_documents"] == 1


async def test_different_keys_create_separate_drafts_listed_newest_first(
    client: httpx.AsyncClient, create_session: CreateSession, repos: Repositories
) -> None:
    session = await create_session()
    await seed_profile(repos, session)
    job = await seed_job(repos, session)

    first = (await post_generation(client, session, job, key="first-key-01")).json()
    second = (await post_generation(client, session, job, key="second-key-1")).json()

    assert first["generation_id"] != second["generation_id"]
    listed = (await client.get("/api/generations", headers=session.headers)).json()["generations"]
    assert [item["generation_id"] for item in listed] == [
        second["generation_id"],
        first["generation_id"],
    ]


# ---- preconditions -----------------------------------------------------------------


async def test_unknown_and_foreign_jobs_are_not_found(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    repos: Repositories,
    fake_provider: FakeProvider,
) -> None:
    alice = await create_session()
    bob = await create_session()
    await seed_profile(repos, alice)
    bobs_job = await seed_job(repos, bob)

    foreign = await post_generation(client, alice, bobs_job, key="foreign-job-1")
    unknown = await client.post(
        "/api/generations", json={"job_id": "no-such-job"}, headers=with_key(alice, "unknown-job-1")
    )

    assert_error(foreign, 404, "not_found")
    assert_error(unknown, 404, "not_found")
    assert fake_provider.calls["generate_documents"] == 0


async def test_invalid_body_is_a_validation_error(
    client: httpx.AsyncClient, create_session: CreateSession
) -> None:
    session = await create_session()
    response = await client.post(
        "/api/generations", json={}, headers=with_key(session, "bad-body-01")
    )
    error = assert_error(response, 422, "validation_error")
    assert error["field_errors"][0]["field"] == "job_id"


@pytest.mark.parametrize(
    ("profile_fields", "code"),
    [
        (None, "profile_not_confirmed"),  # no profile at all
        ({"status": "draft", "index_state": "not_indexed"}, "profile_not_confirmed"),
        ({"index_state": "indexing"}, "profile_not_indexed"),
        ({"index_state": "failed", "index_error": "provider_timeout"}, "profile_not_indexed"),
        # Confirmed at version 2, but only version 1 was ever indexed.
        ({"version": 2, "indexed_version": 1}, "profile_not_indexed"),
    ],
)
async def test_generation_requires_a_confirmed_and_indexed_profile(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    repos: Repositories,
    db: Database,
    fake_provider: FakeProvider,
    profile_fields: dict[str, Any] | None,
    code: str,
) -> None:
    session = await create_session()
    if profile_fields is not None:
        await seed_profile(repos, session, **profile_fields)
    job = await seed_job(repos, session)

    response = await post_generation(client, session, job)

    assert_error(response, 409, code)
    assert fake_provider.calls["embed"] == fake_provider.calls["generate_documents"] == 0
    assert await db["generations"].count_documents({}) == 0
    assert await quota_used(db, session, "generation") == 0


async def test_profile_edited_after_confirmation_blocks_generation(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    repos: Repositories,
    fake_provider: FakeProvider,
) -> None:
    """An edit bumps the version and returns the profile to draft; its new
    content is not indexed, so generating from it is refused."""
    session = await create_session()
    seeded = await seed_profile(repos, session)
    job = await seed_job(repos, session)
    edited = seeded.profile.model_copy(update={"version": 2, "status": "draft"})
    assert await repos.profiles.replace(session.owner_id, edited, expected_version=1)

    assert_error(await post_generation(client, session, job), 409, "profile_not_confirmed")
    assert fake_provider.calls["generate_documents"] == 0


@pytest.mark.parametrize(
    "broken_evidence",
    [
        {"embedding_status": "pending", "embedding": None},
        {"embedding_status": "failed", "embedding": None},
        {"embedding_model": "text-embedding-3-small", "embedding_dimension": 1536},
    ],
)
async def test_partly_indexed_or_mixed_model_evidence_blocks_generation(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    repos: Repositories,
    fake_provider: FakeProvider,
    broken_evidence: dict[str, Any],
) -> None:
    session = await create_session()
    seeded = await seed_profile(repos, session)
    job = await seed_job(repos, session)
    fields = {"embedding_status": "embedded", **broken_evidence}
    extra = make_evidence(session.owner_id, seeded.profile.profile_id, 1, **fields)
    await repos.evidence.insert_many(session.owner_id, [extra])

    assert_error(await post_generation(client, session, job), 409, "profile_not_indexed")
    assert fake_provider.calls["embed"] == fake_provider.calls["generate_documents"] == 0


async def test_only_the_current_profile_versions_evidence_is_used(
    client: httpx.AsyncClient, create_session: CreateSession, repos: Repositories
) -> None:
    """Version 1 claimed Kubernetes; the user removed that and confirmed
    version 2. The old evidence is still stored but must not be used."""
    session = await create_session()
    seeded = await seed_profile(repos, session, version=2)
    old_profile = seeded.profile.model_copy(update={"version": 1})
    text = "Software Engineer at Quillfeather Software: Kubernetes experience with clusters."
    old_evidence = evidence_for_profile(old_profile) + [
        make_evidence(
            session.owner_id,
            seeded.profile.profile_id,
            1,
            evidence_id="old-kubernetes",
            text=text,
            excerpt="Kubernetes experience with clusters.",
            embedding=fake_embedding(text),
            embedding_status="embedded",
        )
    ]
    await repos.evidence.insert_many(session.owner_id, old_evidence)
    job = await seed_job(repos, session)

    body = (await post_generation(client, session, job)).json()

    current_ids = {evidence.evidence_id for evidence in seeded.evidence}
    assert set(body["retrieved_evidence_ids"]) <= current_ids
    for claim in all_claims(body):
        assert set(claim["evidence_ids"]) <= current_ids
    assert "Kubernetes" not in all_text(body)
    coverage = {item["requirement_id"]: item["status"] for item in body["coverage"]}
    assert coverage["req-k8s"] == "missing"


# ---- no evidence -------------------------------------------------------------------


async def test_profile_without_evidence_still_yields_a_valid_draft(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    repos: Repositories,
    fake_provider: FakeProvider,
) -> None:
    session = await create_session()
    await seed_profile(repos, session, records=[])
    job = await seed_job(repos, session)

    response = await post_generation(client, session, job)

    assert response.status_code == 201, response.text
    body = response.json()
    resume = body["resume"]
    assert resume["contact"]["name"] == "Jordan Rivera"
    for section in ("summary", "experience", "projects", "education", "certifications", "skills"):
        assert resume[section] == []
    statuses = [p["validation_status"] for p in body["cover_letter"]["paragraphs"]]
    assert statuses == ["not_applicable", "not_applicable"]
    assert body["retrieved_evidence_ids"] == []
    assert {item["status"] for item in body["coverage"]} == {"missing"}
    assert all(item["evidence_ids"] == [] for item in body["coverage"])
    assert body["coverage_summary"]["percent"] == 0.0
    assert body["warnings"] == [NO_EVIDENCE_WARNING]
    # Nothing to rank, so no embedding call was paid for.
    assert fake_provider.calls["embed"] == 0
    assert body["usage"]["provider_calls"] == 1


async def test_job_without_requirements_has_no_coverage_percentage(
    client: httpx.AsyncClient, create_session: CreateSession, repos: Repositories
) -> None:
    session = await create_session()
    await seed_profile(repos, session)
    job = await seed_job(repos, session, requirements=[])

    body = (await post_generation(client, session, job)).json()

    assert body["coverage"] == []
    assert body["coverage_summary"] == {
        "supported": 0,
        "partial": 0,
        "missing": 0,
        "uncertain": 0,
        "assessed": 0,
        "percent": None,
    }
    assert body["retrieved_evidence_ids"]  # the role query alone still retrieves evidence


async def test_unrelated_profile_gets_missing_coverage_not_invented_content(
    client: httpx.AsyncClient, create_session: CreateSession, repos: Repositories
) -> None:
    session = await create_session()
    records = [record for record in sample_records() if record.record_id == "edu-fairhaven"]
    await seed_profile(repos, session, records=records)
    job = await seed_job(repos, session)

    body = (await post_generation(client, session, job)).json()

    assert {item["status"] for item in body["coverage"]} == {"missing"}
    assert body["coverage_summary"]["percent"] == 0.0
    assert body["resume"]["experience"] == [] and body["resume"]["skills"] == []
    assert [entry["heading"] for entry in body["resume"]["education"]] == [
        "B.S. in Computer Science"
    ]
    for word in ("Kubernetes", "Docker", "Python"):
        assert word not in all_text(body)


# ---- quota -------------------------------------------------------------------------


async def test_generation_quota_is_enforced_per_session(
    make_app: Callable[[Settings], Awaitable[FastAPI]],
    make_settings: Callable[..., Settings],
    create_session: CreateSession,
    repos: Repositories,
) -> None:
    limited = await make_app(make_settings(quota_generation=1))
    provider = limited.state.provider
    session = await create_session()
    await seed_profile(repos, session)
    job = await seed_job(repos, session)

    async with client_for(limited) as limited_client:
        first = await post_generation(limited_client, session, job, key="quota-key-01")
        second = await post_generation(limited_client, session, job, key="quota-key-02")
        replay = await post_generation(limited_client, session, job, key="quota-key-01")

    assert first.status_code == 201
    error = assert_error(second, 429, "quota_exceeded")
    assert error["details"] == {"operation": "generation", "limit": 1}
    assert replay.status_code == 200  # the stored draft is still served
    assert provider.calls["generate_documents"] == 1


async def test_global_daily_ai_limit_stops_generation_before_the_provider(
    make_app: Callable[[Settings], Awaitable[FastAPI]],
    make_settings: Callable[..., Settings],
    create_session: CreateSession,
    repos: Repositories,
    db: Database,
) -> None:
    capped = await make_app(make_settings(global_daily_ai_call_limit=1))
    session = await create_session()
    await seed_profile(repos, session)
    job = await seed_job(repos, session)

    async with client_for(capped) as capped_client:
        response = await post_generation(capped_client, session, job)

    assert_error(response, 429, "quota_exceeded")
    assert capped.state.provider.calls["generate_documents"] == 0
    assert await db["generations"].count_documents({}) == 0


# ---- stale drafts ------------------------------------------------------------------


async def test_profile_and_job_edits_mark_existing_drafts_stale(
    client: httpx.AsyncClient, create_session: CreateSession, repos: Repositories
) -> None:
    session = await create_session()
    seeded = await seed_profile(repos, session)
    job = await seed_job(repos, session)
    created = (await post_generation(client, session, job)).json()
    url = f"/api/generations/{created['generation_id']}"

    async def stale_view() -> tuple[bool, list[str], bool]:
        one = (await client.get(url, headers=session.headers)).json()
        listed = (await client.get("/api/generations", headers=session.headers)).json()
        return one["stale"], one["stale_reasons"], listed["generations"][0]["stale"]

    assert await stale_view() == (False, [], False)

    edited_profile = seeded.profile.model_copy(update={"version": 2, "status": "draft"})
    assert await repos.profiles.replace(session.owner_id, edited_profile, expected_version=1)
    assert await stale_view() == (True, ["profile_changed"], True)

    edited_job = job.model_copy(update={"version": 2})
    assert await repos.jobs.replace(session.owner_id, edited_job, expected_version=1)
    assert await stale_view() == (True, ["profile_changed", "job_changed"], True)

    # The stale draft itself is unchanged and still shows what it was built from.
    stale = (await client.get(url, headers=session.headers)).json()
    assert (stale["profile_version"], stale["job_version"]) == (1, 1)
    assert stale["resume"] == created["resume"]


async def test_draft_is_stale_when_the_profile_was_replaced(
    client: httpx.AsyncClient, create_session: CreateSession, repos: Repositories
) -> None:
    session = await create_session()
    await seed_profile(repos, session)
    job = await seed_job(repos, session)
    created = (await post_generation(client, session, job)).json()

    await repos.profiles.delete_for_owner(session.owner_id)
    await repos.profiles.create(session.owner_id, make_profile(session.owner_id))

    fetched = await client.get(
        f"/api/generations/{created['generation_id']}", headers=session.headers
    )
    assert fetched.json()["stale_reasons"] == ["profile_changed"]

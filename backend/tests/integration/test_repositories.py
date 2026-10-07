"""Repository behaviour against MongoDB: owner scoping, optimistic versions,
the embedding cache lookup and the idempotent generation claim."""

import asyncio
from datetime import timedelta

import pytest

from app.db import Database
from app.repositories import Repositories
from app.repositories.generations import RUNNING_STALE_AFTER
from app.schemas.common import utc_now
from app.schemas.generations import GenerationError
from app.schemas.profiles import IndexProgress
from tests.factories import make_evidence, make_generation, make_job, make_profile, make_source

ALICE = "owner-alice"
BOB = "owner-bob"

# ---- owner scoping -----------------------------------------------------------------


async def test_documents_are_invisible_to_other_owners(repos: Repositories) -> None:
    job = make_job(ALICE)
    evidence = make_evidence(ALICE)
    generation = make_generation(ALICE)
    source = make_source(ALICE)
    await repos.jobs.insert(ALICE, job)
    await repos.evidence.insert_many(ALICE, [evidence])
    await repos.generations.insert(ALICE, generation)
    await repos.sources.insert(ALICE, source)
    await repos.profiles.create(ALICE, make_profile(ALICE))

    assert await repos.jobs.get(ALICE, job.job_id) == job
    assert await repos.jobs.get(BOB, job.job_id) is None
    assert await repos.evidence.get(BOB, evidence.evidence_id) is None
    assert await repos.evidence.get_many(BOB, [evidence.evidence_id]) == []
    assert await repos.generations.get(BOB, generation.generation_id) is None
    assert await repos.sources.get(BOB, source.source_id) is None
    assert await repos.profiles.get_for_owner(BOB) is None
    assert await repos.profiles.exists(BOB) is False

    assert await repos.jobs.list_for_owner(BOB) == []
    assert await repos.generations.list_for_owner(BOB) == []
    assert await repos.sources.list_for_owner(BOB) == []
    assert await repos.evidence.list_for_version(BOB, "profile-1", 1, with_vectors=True) == []


async def test_a_document_cannot_be_stored_under_another_owner(repos: Repositories) -> None:
    with pytest.raises(ValueError):
        await repos.jobs.insert(BOB, make_job(ALICE))
    with pytest.raises(ValueError):
        await repos.evidence.insert_many(BOB, [make_evidence(ALICE)])
    with pytest.raises(ValueError):
        await repos.jobs.replace(BOB, make_job(ALICE), expected_version=1)


async def test_another_owner_cannot_replace_or_modify_a_document(repos: Repositories) -> None:
    job = make_job(ALICE)
    await repos.jobs.insert(ALICE, job)
    hijacked = job.model_copy(update={"owner_id": BOB, "title": "Hijacked", "version": 2})

    assert await repos.jobs.replace(BOB, hijacked, expected_version=1) is None
    assert (await repos.jobs.get(ALICE, job.job_id)).title == "Platform Engineer"

    evidence = make_evidence(ALICE)
    await repos.evidence.insert_many(ALICE, [evidence])
    assert await repos.evidence.set_embeddings(BOB, {evidence.evidence_id: [1.0]}) == 0
    assert await repos.evidence.mark_failed(BOB, [evidence.evidence_id]) == 0
    assert await repos.evidence.delete_version(BOB, "profile-1", 1) == 0


async def test_delete_all_for_owner_reports_counts_and_spares_others(repos: Repositories) -> None:
    for owner in (ALICE, BOB):
        await repos.profiles.create(owner, make_profile(owner))
        await repos.sources.insert(owner, make_source(owner))
        await repos.evidence.insert_many(owner, [make_evidence(owner) for _ in range(3)])
        await repos.jobs.insert(owner, make_job(owner))
        await repos.jobs.insert(owner, make_job(owner))
        await repos.generations.insert(owner, make_generation(owner))

    counts = await repos.delete_all_for_owner(ALICE)

    assert counts.model_dump() == {
        "sources": 1,
        "profiles": 1,
        "evidence": 3,
        "jobs": 2,
        "generations": 1,
    }
    assert (await repos.delete_all_for_owner(ALICE)).model_dump() == {
        "sources": 0,
        "profiles": 0,
        "evidence": 0,
        "jobs": 0,
        "generations": 0,
    }
    assert len(await repos.jobs.list_for_owner(BOB)) == 2
    assert await repos.profiles.exists(BOB) is True


# ---- sources -----------------------------------------------------------------------


async def test_sources_are_listed_in_submission_order_and_replaced_together(
    repos: Repositories,
) -> None:
    first = [make_source(ALICE, label="Notes", position=1), make_source(ALICE, position=0)]
    await repos.sources.replace_for_owner(ALICE, first)
    await repos.sources.insert(BOB, make_source(BOB, label="Bob's resume"))

    assert [source.label for source in await repos.sources.list_for_owner(ALICE)] == [
        "Resume",
        "Notes",
    ]

    replacement = make_source(ALICE, label="LinkedIn", source_type="linkedin", revision=2)
    await repos.sources.replace_for_owner(ALICE, [replacement])

    assert await repos.sources.list_for_owner(ALICE) == [replacement]
    assert [source.label for source in await repos.sources.list_for_owner(BOB)] == ["Bob's resume"]


async def test_source_text_round_trips_unchanged(repos: Repositories) -> None:
    text = "  Jordan Rivera\r\n\tData Engineer – Northwind Labs  "
    source = make_source(ALICE, text=text)
    await repos.sources.insert(ALICE, source)
    assert (await repos.sources.get(ALICE, source.source_id)).text == text


# ---- profiles ----------------------------------------------------------------------


async def test_only_one_profile_can_be_created_per_owner(repos: Repositories) -> None:
    profile = make_profile(ALICE)
    assert await repos.profiles.create(ALICE, profile) is True
    assert await repos.profiles.create(ALICE, make_profile(ALICE)) is False
    assert await repos.profiles.get_for_owner(ALICE) == profile
    assert await repos.profiles.create(BOB, make_profile(BOB)) is True


async def test_profile_replace_requires_the_expected_version(repos: Repositories) -> None:
    profile = make_profile(ALICE)
    await repos.profiles.create(ALICE, profile)
    edited = profile.model_copy(update={"version": 2, "status": "draft"})

    assert await repos.profiles.replace(ALICE, edited, expected_version=1) == edited
    # A second writer that still holds version 1 loses.
    stale_edit = profile.model_copy(update={"version": 2, "index_error": "stale writer"})
    assert await repos.profiles.replace(ALICE, stale_edit, expected_version=1) is None
    assert (await repos.profiles.get_for_owner(ALICE)).index_error is None


async def test_concurrent_profile_edits_produce_exactly_one_winner(repos: Repositories) -> None:
    profile = make_profile(ALICE)
    await repos.profiles.create(ALICE, profile)
    edits = [
        profile.model_copy(update={"version": 2, "index_error": f"writer-{number}"})
        for number in range(6)
    ]

    results = await asyncio.gather(
        *(repos.profiles.replace(ALICE, edit, expected_version=1) for edit in edits)
    )

    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    assert await repos.profiles.get_for_owner(ALICE) == winners[0]


async def test_index_state_updates_apply_only_to_the_matching_version(
    repos: Repositories,
) -> None:
    profile = make_profile(ALICE, version=4)
    await repos.profiles.create(ALICE, profile)
    now = utc_now()

    indexing = await repos.profiles.set_index_state(
        ALICE,
        4,
        index_state="indexing",
        index_progress=IndexProgress(total=10, embedded=4),
        now=now,
    )
    assert indexing.index_state == "indexing"
    assert indexing.index_progress == IndexProgress(total=10, embedded=4)
    assert indexing.status == "draft" and indexing.indexed_version is None
    assert indexing.version == 4

    done = await repos.profiles.set_index_state(
        ALICE,
        4,
        index_state="indexed",
        index_progress=IndexProgress(total=10, embedded=10),
        now=now,
        indexed_version=4,
        status="confirmed",
    )
    assert (done.status, done.index_state, done.indexed_version) == ("confirmed", "indexed", 4)

    # An indexing run that started for an older version must not touch this one.
    outdated = await repos.profiles.set_index_state(
        ALICE,
        3,
        index_state="failed",
        index_progress=IndexProgress(),
        now=now,
        index_error="late failure",
    )
    assert outdated is None
    assert (await repos.profiles.get_for_owner(ALICE)).index_state == "indexed"


# ---- evidence ----------------------------------------------------------------------


async def test_evidence_listing_is_limited_to_one_profile_version(repos: Repositories) -> None:
    version_1 = [make_evidence(ALICE, "p1", 1, position=index) for index in range(2)]
    version_2 = [make_evidence(ALICE, "p1", 2, position=index) for index in (2, 0, 1)]
    await repos.evidence.insert_many(ALICE, version_1 + version_2)
    await repos.evidence.insert_many(BOB, [make_evidence(BOB, "p1", 2)])

    listed = await repos.evidence.list_for_version(ALICE, "p1", 2, with_vectors=False)

    assert {doc.profile_version for doc in listed} == {2}
    assert [doc.position for doc in listed] == [0, 1, 2]
    assert {doc.owner_id for doc in listed} == {ALICE}
    assert await repos.evidence.list_for_version(ALICE, "p1", 3, with_vectors=False) == []


async def test_vectors_are_loaded_only_on_request(repos: Repositories, db: Database) -> None:
    vector = [0.25] * 256
    evidence = make_evidence(ALICE, embedding=vector, embedding_status="embedded")
    await repos.evidence.insert_many(ALICE, [evidence])

    without = await repos.evidence.list_for_version(ALICE, "profile-1", 1, with_vectors=False)
    with_vectors = await repos.evidence.list_for_version(ALICE, "profile-1", 1, with_vectors=True)
    single = await repos.evidence.get(ALICE, evidence.evidence_id)
    many = await repos.evidence.get_many(ALICE, [evidence.evidence_id, "missing-id"])
    many_with = await repos.evidence.get_many(ALICE, [evidence.evidence_id], with_vectors=True)

    assert without[0].embedding is None
    assert with_vectors[0].embedding == vector
    assert single.embedding is None and single.embedding_status == "embedded"
    assert len(many) == 1 and many[0].embedding is None
    assert many_with[0].embedding == vector
    # The vector is still stored; it was only left out of the query result.
    assert (await db["evidence"].find_one({"_id": evidence.evidence_id}))["embedding"] == vector


async def test_embeddings_are_stored_and_statuses_counted(repos: Repositories) -> None:
    documents = [make_evidence(ALICE, position=index) for index in range(4)]
    await repos.evidence.insert_many(ALICE, documents)
    assert await repos.evidence.count_by_status(ALICE, "profile-1", 1) == {
        "pending": 4,
        "embedded": 0,
        "failed": 0,
    }

    stored = await repos.evidence.set_embeddings(
        ALICE,
        {documents[0].evidence_id: [0.1, 0.2], documents[1].evidence_id: [0.3, 0.4]},
    )
    failed = await repos.evidence.mark_failed(ALICE, [documents[2].evidence_id])

    assert (stored, failed) == (2, 1)
    assert await repos.evidence.set_embeddings(ALICE, {}) == 0
    assert await repos.evidence.count_by_status(ALICE, "profile-1", 1) == {
        "pending": 1,
        "embedded": 2,
        "failed": 1,
    }
    listed = await repos.evidence.list_for_version(ALICE, "profile-1", 1, with_vectors=True)
    assert [doc.embedding for doc in listed] == [[0.1, 0.2], [0.3, 0.4], None, None]
    assert [doc.embedding_status for doc in listed] == ["embedded", "embedded", "failed", "pending"]


async def test_reusable_embeddings_match_owner_hash_model_and_dimension(
    repos: Repositories,
) -> None:
    await repos.evidence.insert_many(
        ALICE,
        [
            make_evidence(ALICE, text="alpha", embedding=[1.0], embedding_status="embedded"),
            # Same content, but never embedded.
            make_evidence(ALICE, text="beta"),
            # Embedded with a different model: must never be reused.
            make_evidence(
                ALICE,
                text="gamma",
                embedding=[3.0],
                embedding_status="embedded",
                embedding_model="text-embedding-3-small",
                embedding_dimension=1536,
            ),
            # Same model name but another dimension.
            make_evidence(
                ALICE,
                text="delta",
                embedding=[4.0],
                embedding_status="embedded",
                embedding_dimension=128,
            ),
        ],
    )
    await repos.evidence.insert_many(
        BOB, [make_evidence(BOB, text="epsilon", embedding=[5.0], embedding_status="embedded")]
    )
    hashes = [
        make_evidence(ALICE, text=text).content_hash
        for text in ("alpha", "beta", "gamma", "delta", "epsilon")
    ]

    reusable = await repos.evidence.find_reusable_embeddings(
        ALICE, hashes, "fake-embedding-256", 256
    )
    other_model = await repos.evidence.find_reusable_embeddings(
        ALICE, hashes, "text-embedding-3-small", 1536
    )

    assert reusable == {hashes[0]: [1.0]}
    assert other_model == {hashes[2]: [3.0]}
    assert await repos.evidence.find_reusable_embeddings(ALICE, [], "fake-embedding-256", 256) == {}


async def test_old_versions_stay_until_deleted_explicitly(repos: Repositories) -> None:
    await repos.evidence.insert_many(
        ALICE, [make_evidence(ALICE, "p1", 1), make_evidence(ALICE, "p1", 2)]
    )

    assert await repos.evidence.delete_version(ALICE, "p1", 2) == 1

    assert len(await repos.evidence.list_for_version(ALICE, "p1", 1, with_vectors=False)) == 1
    assert await repos.evidence.list_for_version(ALICE, "p1", 2, with_vectors=False) == []


# ---- jobs --------------------------------------------------------------------------


async def test_jobs_are_listed_newest_first(repos: Repositories) -> None:
    now = utc_now()
    older = make_job(ALICE, title="Older", created_at=now - timedelta(minutes=5))
    newer = make_job(ALICE, title="Newer", created_at=now)
    await repos.jobs.insert(ALICE, older)
    await repos.jobs.insert(ALICE, newer)

    assert [job.title for job in await repos.jobs.list_for_owner(ALICE)] == ["Newer", "Older"]
    assert [job.title for job in await repos.jobs.list_for_owner(ALICE, limit=1)] == ["Newer"]


async def test_job_replace_requires_the_expected_version(repos: Repositories) -> None:
    job = make_job(ALICE)
    await repos.jobs.insert(ALICE, job)
    edited = job.model_copy(update={"version": 2, "title": "Senior Platform Engineer"})

    assert await repos.jobs.replace(ALICE, edited, expected_version=1) == edited
    assert await repos.jobs.replace(ALICE, edited, expected_version=1) is None
    missing = make_job(ALICE)
    assert await repos.jobs.replace(ALICE, missing, expected_version=1) is None


# ---- generations: idempotent claim -------------------------------------------------


async def test_first_claim_inserts_a_running_record(repos: Repositories) -> None:
    candidate = make_generation(ALICE, idempotency_key="key-first")

    document, claimed = await repos.generations.claim(ALICE, candidate, utc_now())

    assert claimed is True
    assert document == candidate
    stored = await repos.generations.get(ALICE, candidate.generation_id)
    assert (stored.status, stored.attempt) == ("running", 1)


async def test_repeated_key_returns_the_existing_record_without_claiming(
    repos: Repositories,
) -> None:
    now = utc_now()
    first, _ = await repos.generations.claim(
        ALICE, make_generation(ALICE, idempotency_key="key-repeat"), now
    )

    # While the first request is still running.
    running, claimed_running = await repos.generations.claim(
        ALICE, make_generation(ALICE, idempotency_key="key-repeat"), now
    )
    assert claimed_running is False
    assert (running.generation_id, running.status) == (first.generation_id, "running")

    # After it completed.
    finished = first.model_copy(update={"status": "completed", "warnings": ["done"]})
    assert await repos.generations.complete(ALICE, finished) == finished
    completed, claimed_completed = await repos.generations.claim(
        ALICE, make_generation(ALICE, idempotency_key="key-repeat"), now
    )
    assert claimed_completed is False
    assert completed == finished
    assert len(await repos.generations.list_for_owner(ALICE)) == 1


async def test_simultaneous_requests_with_one_key_yield_a_single_claim(
    repos: Repositories,
) -> None:
    now = utc_now()
    candidates = [make_generation(ALICE, idempotency_key="key-race") for _ in range(8)]

    results = await asyncio.gather(
        *(repos.generations.claim(ALICE, candidate, now) for candidate in candidates)
    )

    assert sum(claimed for _, claimed in results) == 1
    assert len({document.generation_id for document, _ in results}) == 1
    assert len(await repos.generations.list_for_owner(ALICE)) == 1


async def test_failed_generation_can_be_retried_with_the_same_key(repos: Repositories) -> None:
    now = utc_now()
    first, _ = await repos.generations.claim(
        ALICE, make_generation(ALICE, idempotency_key="key-retry"), now
    )
    error = GenerationError(code="provider_timeout", message="The AI provider took too long.")
    assert await repos.generations.fail(ALICE, first.generation_id, first.attempt, error, now)
    failed = await repos.generations.get(ALICE, first.generation_id)
    assert (failed.status, failed.error) == ("failed", error)

    retry_candidate = make_generation(
        ALICE, idempotency_key="key-retry", job_version=2, profile_version=5
    )
    retried, claimed = await repos.generations.claim(ALICE, retry_candidate, now)

    assert claimed is True
    assert retried.generation_id == first.generation_id  # same record, not a new one
    assert (retried.status, retried.attempt, retried.error) == ("running", 2, None)
    # The retry runs against the versions that are current now.
    assert (retried.job_version, retried.profile_version) == (2, 5)
    assert retried.created_at == first.created_at


async def test_only_one_of_several_simultaneous_retries_takes_over(repos: Repositories) -> None:
    now = utc_now()
    first, _ = await repos.generations.claim(
        ALICE, make_generation(ALICE, idempotency_key="key-retry-race"), now
    )
    error = GenerationError(code="provider_unavailable", message="Could not reach the provider.")
    await repos.generations.fail(ALICE, first.generation_id, first.attempt, error, now)

    results = await asyncio.gather(
        *(
            repos.generations.claim(
                ALICE, make_generation(ALICE, idempotency_key="key-retry-race"), now
            )
            for _ in range(6)
        )
    )

    assert sum(claimed for _, claimed in results) == 1
    assert (await repos.generations.get(ALICE, first.generation_id)).attempt == 2


async def test_running_record_older_than_five_minutes_is_taken_over(repos: Repositories) -> None:
    assert timedelta(minutes=5) == RUNNING_STALE_AFTER
    started = utc_now()
    abandoned, _ = await repos.generations.claim(
        ALICE, make_generation(ALICE, idempotency_key="key-stale", started_at=started), started
    )

    # Just under five minutes later the first run still owns the key.
    _, claimed_early = await repos.generations.claim(
        ALICE,
        make_generation(ALICE, idempotency_key="key-stale"),
        started + timedelta(minutes=4, seconds=59),
    )
    assert claimed_early is False

    later = started + timedelta(minutes=5, seconds=1)
    taken, claimed_late = await repos.generations.claim(
        ALICE, make_generation(ALICE, idempotency_key="key-stale"), later
    )
    assert claimed_late is True
    assert (taken.generation_id, taken.attempt, taken.started_at) == (
        abandoned.generation_id,
        2,
        later,
    )

    # The abandoned first attempt finishes late: its result is discarded.
    late_result = abandoned.model_copy(update={"status": "completed", "warnings": ["late"]})
    assert await repos.generations.complete(ALICE, late_result) is None
    error = GenerationError(code="internal_error", message="late failure")
    assert not await repos.generations.fail(ALICE, abandoned.generation_id, 1, error, later)
    assert (await repos.generations.get(ALICE, abandoned.generation_id)).status == "running"

    # The attempt that took over can still complete.
    result = taken.model_copy(update={"status": "completed"})
    assert await repos.generations.complete(ALICE, result) == result


async def test_same_key_from_different_owners_is_independent(repos: Repositories) -> None:
    now = utc_now()
    alice_doc, alice_claimed = await repos.generations.claim(
        ALICE, make_generation(ALICE, idempotency_key="shared-key"), now
    )
    bob_doc, bob_claimed = await repos.generations.claim(
        BOB, make_generation(BOB, idempotency_key="shared-key"), now
    )
    assert alice_claimed and bob_claimed
    assert alice_doc.generation_id != bob_doc.generation_id


async def test_claim_refuses_a_candidate_of_another_owner(repos: Repositories) -> None:
    with pytest.raises(ValueError):
        await repos.generations.claim(BOB, make_generation(ALICE), utc_now())


# ---- generations: edits ------------------------------------------------------------


async def test_generation_save_requires_the_expected_revision(repos: Repositories) -> None:
    generation = make_generation(ALICE, status="completed")
    await repos.generations.insert(ALICE, generation)
    edited = generation.model_copy(update={"revision": 2, "warnings": ["edited"]})

    assert await repos.generations.save(ALICE, edited, expected_revision=1) == edited
    assert await repos.generations.save(ALICE, edited, expected_revision=1) is None
    assert await repos.generations.save(BOB, edited.model_copy(update={"owner_id": BOB}), 2) is None


async def test_generations_are_listed_newest_first(repos: Repositories) -> None:
    now = utc_now()
    await repos.generations.insert(
        ALICE, make_generation(ALICE, job_title="Older", created_at=now - timedelta(minutes=1))
    )
    await repos.generations.insert(ALICE, make_generation(ALICE, job_title="Newer", created_at=now))
    listed = await repos.generations.list_for_owner(ALICE)
    assert [generation.job_title for generation in listed] == ["Newer", "Older"]


async def test_regeneration_key_can_be_claimed_once_and_released(repos: Repositories) -> None:
    generation = make_generation(ALICE, status="completed")
    await repos.generations.insert(ALICE, generation)
    generation_id = generation.generation_id

    assert await repos.generations.claim_regeneration_key(ALICE, generation_id, "regen-1") is True
    assert await repos.generations.claim_regeneration_key(ALICE, generation_id, "regen-1") is False
    assert await repos.generations.claim_regeneration_key(ALICE, generation_id, "regen-2") is True
    assert await repos.generations.claim_regeneration_key(BOB, generation_id, "regen-3") is False
    assert (await repos.generations.get(ALICE, generation_id)).regeneration_keys == [
        "regen-1",
        "regen-2",
    ]

    await repos.generations.release_regeneration_key(ALICE, generation_id, "regen-1")
    assert await repos.generations.claim_regeneration_key(ALICE, generation_id, "regen-1") is True


async def test_simultaneous_regenerations_with_one_key_claim_once(repos: Repositories) -> None:
    generation = make_generation(ALICE, status="completed")
    await repos.generations.insert(ALICE, generation)

    results = await asyncio.gather(
        *(
            repos.generations.claim_regeneration_key(ALICE, generation.generation_id, "regen-x")
            for _ in range(6)
        )
    )

    assert sum(results) == 1

"""Retrieval against the real database: owner and version scoping, bounds,
relevance and the single batched embedding call."""

from collections.abc import Awaitable, Callable

import pytest

from app.errors import ProfileNotIndexed
from app.providers.base import Usage
from app.providers.fake.embeddings import fake_embedding
from app.providers.fake.provider import FakeProvider
from app.repositories import Repositories
from app.schemas.documents import JobDoc
from app.services.retrieval import (
    PythonRetriever,
    RetrievalFilters,
    RetrievalResult,
    retrieve_for_job,
)
from app.services.textutil import estimate_tokens
from tests.conftest import SessionHandle
from tests.factories import make_evidence, make_job
from tests.helpers_generation import (
    SeededProfile,
    evidence_for_profile,
    requirement,
    sample_requirements,
    seed_profile,
)

CreateSession = Callable[[], Awaitable[SessionHandle]]


def job_for(owner_id: str) -> JobDoc:
    return make_job(
        owner_id,
        title="Platform Engineer",
        role_summary="Run the container platform with Python and Docker.",
        requirements=sample_requirements(),
    )


async def retrieve(
    repos: Repositories,
    provider: FakeProvider,
    seeded: SeededProfile,
    *,
    version: int | None = None,
    per_requirement: int = 4,
    max_context: int = 18,
    token_budget: int = 6000,
) -> RetrievalResult:
    profile = seeded.profile
    return await retrieve_for_job(
        PythonRetriever(repos.evidence),
        provider,
        profile.owner_id,
        version or profile.version,
        job_for(profile.owner_id),
        RetrievalFilters(
            profile_id=profile.profile_id,
            embedding_model=provider.embedding_model,
            embedding_dimension=provider.embedding_dimension,
        ),
        per_requirement=per_requirement,
        max_context=max_context,
        token_budget=token_budget,
    )


async def test_relevant_evidence_is_retrieved_for_each_requirement(
    repos: Repositories, create_session: CreateSession, fake_provider: FakeProvider
) -> None:
    seeded = await seed_profile(repos, await create_session())
    result = await retrieve(repos, fake_provider, seeded)

    assert result.profile_version == 1
    # The bullet that answers each requirement is among its candidates, and
    # the best candidate for "Docker" is evidence that mentions Docker.
    assert seeded.evidence_id("b-docker") in result.candidates_by_requirement["req-docker"]
    assert seeded.evidence_id("b-pipelines") in result.candidates_by_requirement["req-python"]
    texts = {evidence.evidence_id: evidence.text for evidence in result.evidence}
    assert "Docker" in texts[result.candidates_by_requirement["req-docker"][0]]
    assert "Python" in texts[result.candidates_by_requirement["req-python"][0]]
    selected = [evidence.evidence_id for evidence in result.evidence]
    # The context is in profile order and carries no vectors.
    assert selected == [e.evidence_id for e in seeded.evidence if e.evidence_id in selected]
    assert all(evidence.embedding is None for evidence in result.evidence)
    # Every listed candidate is part of the selected context.
    for candidates in result.candidates_by_requirement.values():
        assert set(candidates) <= set(selected)


async def test_all_queries_are_embedded_in_one_provider_call(
    repos: Repositories, create_session: CreateSession, fake_provider: FakeProvider
) -> None:
    seeded = await seed_profile(repos, await create_session())
    result = await retrieve(repos, fake_provider, seeded)

    # Three requirements plus the role query, one call.
    assert fake_provider.calls["embed"] == 1
    assert result.usage.provider_calls == 1
    assert result.usage.embedding_tokens > 0


async def test_contact_details_in_a_posting_are_not_sent_for_embedding(
    repos: Repositories,
    create_session: CreateSession,
    fake_provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seeded = await seed_profile(repos, await create_session())
    profile = seeded.profile
    contact_only = "https://example.com/apply"
    job = make_job(
        profile.owner_id,
        title="Platform Engineer",
        role_summary="Run the platform. Questions to recruiter@example.com or (412) 555-0199.",
        requirements=[
            requirement("req-docker", "Docker, see https://example.com/stack for details"),
            requirement("req-link", contact_only),
        ],
    )
    sent: list[str] = []
    embed = fake_provider.embed

    async def recording_embed(texts: list[str]) -> tuple[list[list[float]], Usage]:
        sent.extend(texts)
        return await embed(texts)

    monkeypatch.setattr(fake_provider, "embed", recording_embed)
    result = await retrieve_for_job(
        PythonRetriever(repos.evidence),
        fake_provider,
        profile.owner_id,
        profile.version,
        job,
        RetrievalFilters(
            profile_id=profile.profile_id,
            embedding_model=fake_provider.embedding_model,
            embedding_dimension=fake_provider.embedding_dimension,
        ),
        per_requirement=4,
        max_context=18,
        token_budget=6000,
    )

    assert sent == [
        "Docker, see for details",
        contact_only,  # nothing else to embed, so it is kept rather than sent empty
        "Platform Engineer. Run the platform. Questions to or .",
    ]
    assert seeded.evidence_id("b-docker") in result.candidates_by_requirement["req-docker"]


async def test_another_owners_evidence_is_never_returned(
    repos: Repositories, create_session: CreateSession, fake_provider: FakeProvider
) -> None:
    alice = await create_session()
    bob = await create_session()
    seeded_alice = await seed_profile(repos, alice)
    seeded_bob = await seed_profile(repos, bob)
    # Bob's profile has the perfect match for the Kubernetes requirement, and
    # even claims Alice's profile ID and version. Only the owner differs.
    text = "Kubernetes experience running production clusters"
    await repos.evidence.insert_many(
        bob.owner_id,
        [
            make_evidence(
                bob.owner_id,
                seeded_alice.profile.profile_id,
                seeded_alice.profile.version,
                evidence_id="bob-kubernetes",
                text=text,
                embedding=fake_embedding(text),
                embedding_status="embedded",
            )
        ],
    )

    result = await retrieve(repos, fake_provider, seeded_alice)

    alice_ids = {evidence.evidence_id for evidence in seeded_alice.evidence}
    bob_ids = {evidence.evidence_id for evidence in seeded_bob.evidence} | {"bob-kubernetes"}
    returned = {evidence.evidence_id for evidence in result.evidence}
    assert returned and returned <= alice_ids
    assert not returned & bob_ids
    assert all(evidence.owner_id == alice.owner_id for evidence in result.evidence)
    for candidates in result.candidates_by_requirement.values():
        assert not set(candidates) & bob_ids


async def test_another_profile_versions_evidence_is_never_returned(
    repos: Repositories, create_session: CreateSession, fake_provider: FakeProvider
) -> None:
    session = await create_session()
    seeded = await seed_profile(repos, session, version=2)
    # Version 1 is still stored (old drafts cite it) and contains a Kubernetes
    # statement that was removed from the profile in version 2.
    old_profile = seeded.profile.model_copy(update={"version": 1})
    text = "Kubernetes experience running production clusters"
    old_evidence = evidence_for_profile(old_profile) + [
        make_evidence(
            session.owner_id,
            seeded.profile.profile_id,
            1,
            evidence_id="old-kubernetes",
            text=text,
            embedding=fake_embedding(text),
            embedding_status="embedded",
        )
    ]
    await repos.evidence.insert_many(session.owner_id, old_evidence)

    current = await retrieve(repos, fake_provider, seeded)
    assert current.evidence
    assert {evidence.profile_version for evidence in current.evidence} == {2}
    assert "old-kubernetes" not in {evidence.evidence_id for evidence in current.evidence}

    # Asking for version 1 explicitly returns version 1 only.
    old = await retrieve(repos, fake_provider, seeded, version=1)
    assert {evidence.profile_version for evidence in old.evidence} == {1}
    assert old.candidates_by_requirement["req-k8s"][0] == "old-kubernetes"


async def test_context_is_bounded_by_record_count_and_token_budget(
    repos: Repositories, create_session: CreateSession, fake_provider: FakeProvider
) -> None:
    seeded = await seed_profile(repos, await create_session())

    unbounded = await retrieve(repos, fake_provider, seeded)
    assert len(unbounded.evidence) > 2

    two_records = await retrieve(repos, fake_provider, seeded, max_context=2)
    assert len(two_records.evidence) == 2

    small_budget = await retrieve(repos, fake_provider, seeded, token_budget=30)
    used = sum(estimate_tokens(evidence.text) for evidence in small_budget.evidence)
    assert 0 < used <= 30
    assert len(small_budget.evidence) < len(unbounded.evidence)

    one_each = await retrieve(repos, fake_provider, seeded, per_requirement=1)
    assert all(len(ids) <= 1 for ids in one_each.candidates_by_requirement.values())


async def test_profile_without_evidence_gives_an_empty_context(
    repos: Repositories, create_session: CreateSession, fake_provider: FakeProvider
) -> None:
    seeded = await seed_profile(repos, await create_session(), records=[])
    result = await retrieve(repos, fake_provider, seeded)
    assert result.evidence == []
    assert result.candidates_by_requirement == {"req-python": [], "req-docker": [], "req-k8s": []}


async def test_evidence_embedded_with_another_model_is_refused(
    repos: Repositories, create_session: CreateSession, fake_provider: FakeProvider
) -> None:
    session = await create_session()
    seeded = await seed_profile(repos, session)
    await repos.evidence.insert_many(
        session.owner_id,
        [
            make_evidence(
                session.owner_id,
                seeded.profile.profile_id,
                1,
                embedding_model="text-embedding-3-small",
                embedding_dimension=1536,
                embedding=[0.1] * 1536,
                embedding_status="embedded",
            )
        ],
    )
    with pytest.raises(ProfileNotIndexed):
        await retrieve(repos, fake_provider, seeded)

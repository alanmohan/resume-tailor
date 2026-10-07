"""Failure handling of generation: provider errors, database errors, abandoned
runs, expired or revoked sessions, and data cleared while a generation runs."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from pymongo.errors import AutoReconnect

from app.db import Database
from app.providers.base import (
    LLMGenerationContext,
    ProviderError,
    ProviderInvalidOutput,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderUnavailable,
)
from app.providers.fake.provider import FakeProvider
from app.repositories import Repositories
from app.repositories.generations import GenerationRepository
from app.schemas.common import utc_now
from app.services.generation import CORRECTION_FAILED_WARNING, UNEXPECTED_FAILURE_MESSAGE
from tests.conftest import SessionHandle
from tests.factories import make_generation
from tests.helpers import assert_error
from tests.helpers_generation import (
    all_text,
    entry_for,
    evidence_alias,
    install_scripted_provider,
    llm_entry,
    llm_generation,
    owned_counts,
    post_generation,
    record_alias,
    seed_job,
    seed_profile,
    wait_for_call,
    with_key,
)

CreateSession = Callable[[], Awaitable[SessionHandle]]

EMPTY = {"sources": 0, "profiles": 0, "evidence": 0, "jobs": 0, "generations": 0}


# ---- provider failures -------------------------------------------------------------


@pytest.mark.parametrize(
    ("operation", "error", "status", "code"),
    [
        (
            "generate_documents",
            ProviderTimeout("The AI provider took too long."),
            504,
            "provider_timeout",
        ),
        (
            "generate_documents",
            ProviderRateLimited("The AI provider is busy."),
            503,
            "provider_rate_limited",
        ),
        (
            "generate_documents",
            ProviderInvalidOutput("Incomplete response."),
            502,
            "provider_invalid_output",
        ),
        ("embed", ProviderRateLimited("The AI provider is busy."), 503, "provider_rate_limited"),
        (
            "embed",
            ProviderUnavailable("Could not reach the AI provider."),
            502,
            "provider_unavailable",
        ),
    ],
)
async def test_provider_failure_marks_the_generation_failed_and_a_retry_succeeds(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    repos: Repositories,
    db: Database,
    fake_provider: FakeProvider,
    operation: str,
    error: ProviderError,
    status: int,
    code: str,
) -> None:
    session = await create_session()
    seeded = await seed_profile(repos, session)
    job = await seed_job(repos, session)
    before = await owned_counts(db, session.owner_id)
    fake_provider.fail_next(operation, error)

    failed = await post_generation(client, session, job, key="retry-key-01")

    envelope = assert_error(failed, status, code)
    assert envelope["retryable"] is True
    assert envelope["message"] == error.message
    # The failure is recorded on the generation, with a safe message only.
    listed = (await client.get("/api/generations", headers=session.headers)).json()["generations"]
    assert [item["status"] for item in listed] == ["failed"]
    stored = (
        await client.get(f"/api/generations/{listed[0]['generation_id']}", headers=session.headers)
    ).json()
    assert stored["error"] == {"code": code, "message": error.message}
    assert (stored["resume"], stored["cover_letter"], stored["coverage"]) == (None, None, [])
    # Everything completed earlier is still there.
    after = await owned_counts(db, session.owner_id)
    assert after == {**before, "generations": 1}
    assert (await repos.profiles.get_for_owner(session.owner_id)).version == seeded.profile.version

    retried = await post_generation(client, session, job, key="retry-key-01")

    assert retried.status_code == 201, retried.text
    body = retried.json()
    assert body["generation_id"] == stored["generation_id"]  # same record, second attempt
    assert (body["status"], body["error"]) == ("completed", None)
    assert body["resume"]["experience"]
    assert (await db["generations"].find_one({"_id": body["generation_id"]}))["attempt"] == 2
    assert await db["generations"].count_documents({"owner_id": session.owner_id}) == 1


async def test_failed_generation_has_no_documents_to_edit_or_validate(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    repos: Repositories,
    fake_provider: FakeProvider,
) -> None:
    session = await create_session()
    await seed_profile(repos, session)
    job = await seed_job(repos, session)
    fake_provider.fail_next("generate_documents", ProviderTimeout("Too slow."))
    await post_generation(client, session, job)
    listed = (await client.get("/api/generations", headers=session.headers)).json()["generations"]
    url = f"/api/generations/{listed[0]['generation_id']}"

    patched = await client.patch(url, json={"expected_revision": 1}, headers=session.headers)
    validated = await client.post(f"{url}/validate", headers=session.headers)

    assert_error(patched, 422, "validation_error")
    assert_error(validated, 422, "validation_error")


async def test_failed_correction_pass_keeps_the_first_draft(
    app: FastAPI, client: httpx.AsyncClient, create_session: CreateSession, repos: Repositories
) -> None:
    """The first draft is paid for. If the correction pass fails, its
    unsupported statements are removed instead of losing the whole draft."""
    provider = install_scripted_provider(app)
    session = await create_session()
    await seed_profile(repos, session)
    job = await seed_job(repos, session)

    def first_draft(ctx: LLMGenerationContext) -> Any:
        role = record_alias(ctx, "Software Engineer")
        docker = evidence_alias(ctx, "Docker for")
        return llm_generation(
            experience=[
                llm_entry(
                    role,
                    ("Containerised services with Docker.", [docker]),
                    ("Ran Kubernetes clusters in production.", [docker]),
                )
            ]
        )

    def failing_correction(ctx: LLMGenerationContext) -> Any:
        raise ProviderTimeout("The AI provider took too long.")

    provider.queue_generation(first_draft, failing_correction)

    response = await post_generation(client, session, job)

    assert response.status_code == 201, response.text
    body = response.json()
    assert provider.calls["generate_documents"] == 2
    bullets = [bullet["text"] for bullet in entry_for(body, "experience", "role-quill")["bullets"]]
    assert bullets == ["Containerised services with Docker."]
    assert [claim["text"] for claim in body["omitted_claims"]] == [
        "Ran Kubernetes clusters in production."
    ]
    assert CORRECTION_FAILED_WARNING in body["warnings"]
    assert "Kubernetes" not in all_text(body)


async def test_unexpected_error_is_recorded_without_its_message(
    app: FastAPI,
    client: httpx.AsyncClient,
    create_session: CreateSession,
    repos: Repositories,
    db: Database,
) -> None:
    provider = install_scripted_provider(app)
    session = await create_session()
    await seed_profile(repos, session)
    job = await seed_job(repos, session)

    def broken(ctx: LLMGenerationContext) -> Any:
        raise RuntimeError("secret detail: Jordan Rivera's resume text")

    provider.queue_generation(broken)

    failed = await post_generation(client, session, job, key="broken-key-1")

    assert_error(failed, 500, "internal_error")
    assert "Jordan" not in failed.text
    stored = await db["generations"].find_one({"owner_id": session.owner_id})
    assert stored["status"] == "failed"
    assert stored["error"] == {"code": "internal_error", "message": UNEXPECTED_FAILURE_MESSAGE}
    retried = await post_generation(client, session, job, key="broken-key-1")
    assert retried.status_code == 201


# ---- database failure --------------------------------------------------------------


async def test_database_failure_while_saving_is_reported_and_retryable(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    repos: Repositories,
    db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = await create_session()
    await seed_profile(repos, session)
    job = await seed_job(repos, session)
    before = await owned_counts(db, session.owner_id)

    async def connection_lost(*args: Any, **kwargs: Any) -> None:
        raise AutoReconnect("connection reset by peer")

    with monkeypatch.context() as patch:
        patch.setattr(GenerationRepository, "complete", connection_lost)
        failed = await post_generation(client, session, job, key="db-down-key")

    error = assert_error(failed, 503, "database_unavailable")
    assert error["retryable"] is True
    assert "connection reset" not in failed.text
    stored = await db["generations"].find_one({"owner_id": session.owner_id})
    assert (stored["status"], stored["error"]["code"]) == ("failed", "database_unavailable")
    assert await owned_counts(db, session.owner_id) == {**before, "generations": 1}

    retried = await post_generation(client, session, job, key="db-down-key")
    assert retried.status_code == 201
    assert retried.json()["generation_id"] == stored["_id"]


# ---- abandoned runs ----------------------------------------------------------------


async def test_running_record_older_than_five_minutes_is_taken_over(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    repos: Repositories,
    db: Database,
    fake_provider: FakeProvider,
) -> None:
    """A process that died mid-generation leaves a "running" record behind."""
    session = await create_session()
    seeded = await seed_profile(repos, session)
    job = await seed_job(repos, session)
    abandoned = make_generation(
        session.owner_id,
        idempotency_key="stuck-key-01",
        job_id=job.job_id,
        profile_id=seeded.profile.profile_id,
        started_at=utc_now() - timedelta(minutes=5, seconds=5),
        expires_at=session.expires_at,
    )
    await repos.generations.insert(session.owner_id, abandoned)

    response = await post_generation(client, session, job, key="stuck-key-01")

    assert response.status_code == 201, response.text
    assert response.json()["generation_id"] == abandoned.generation_id
    assert (await db["generations"].find_one({"_id": abandoned.generation_id}))["attempt"] == 2
    assert fake_provider.calls["generate_documents"] == 1


async def test_recently_started_run_is_still_in_progress(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    repos: Repositories,
    db: Database,
    fake_provider: FakeProvider,
) -> None:
    session = await create_session()
    seeded = await seed_profile(repos, session)
    job = await seed_job(repos, session)
    running = make_generation(
        session.owner_id,
        idempotency_key="busy-key-001",
        job_id=job.job_id,
        profile_id=seeded.profile.profile_id,
        started_at=utc_now() - timedelta(minutes=4),
        expires_at=session.expires_at,
    )
    await repos.generations.insert(session.owner_id, running)

    response = await post_generation(client, session, job, key="busy-key-001")

    error = assert_error(response, 409, "generation_in_progress")
    assert error["details"] == {"generation_id": running.generation_id}
    assert fake_provider.calls["generate_documents"] == 0
    stored = await db["sessions"].find_one({"_id": session.session_id})
    assert stored["quota"]["generation"] == 0
    # Its documents do not exist yet, so it cannot be edited or validated.
    url = f"/api/generations/{running.generation_id}"
    patched = await client.patch(url, json={"expected_revision": 1}, headers=session.headers)
    assert_error(patched, 409, "generation_in_progress")
    validated = await client.post(f"{url}/validate", headers=session.headers)
    assert_error(validated, 409, "generation_in_progress")


# ---- sessions ----------------------------------------------------------------------


async def test_clearing_data_during_a_slow_generation_leaves_nothing_behind(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    repos: Repositories,
    db: Database,
    fake_provider: FakeProvider,
) -> None:
    session = await create_session()
    await seed_profile(repos, session)
    job = await seed_job(repos, session)
    fake_provider.set_delay("generate_documents", 0.5)

    in_flight = asyncio.create_task(post_generation(client, session, job))
    await wait_for_call(fake_provider, "generate_documents")  # now waiting for the provider
    assert not in_flight.done()
    assert (await owned_counts(db, session.owner_id))["generations"] == 1  # the running record

    cleared = await client.delete("/api/session", headers=session.headers)
    assert cleared.status_code == 200
    assert cleared.json()["deleted_counts"]["generations"] == 1

    response = await in_flight

    assert_error(response, 401, "unauthorized")
    assert fake_provider.calls["generate_documents"] == 1
    assert await owned_counts(db, session.owner_id) == EMPTY


async def test_clearing_data_during_a_failing_generation_leaves_nothing_behind(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    repos: Repositories,
    db: Database,
    fake_provider: FakeProvider,
) -> None:
    session = await create_session()
    await seed_profile(repos, session)
    job = await seed_job(repos, session)
    fake_provider.set_delay("generate_documents", 0.5)
    fake_provider.fail_next("generate_documents", ProviderTimeout("Too slow."))

    in_flight = asyncio.create_task(post_generation(client, session, job))
    await wait_for_call(fake_provider, "generate_documents")
    await client.delete("/api/session", headers=session.headers)
    response = await in_flight

    assert_error(response, 401, "unauthorized")
    assert await owned_counts(db, session.owner_id) == EMPTY


async def generation_requests(
    client: httpx.AsyncClient, session: SessionHandle, generation_id: str, job_id: str
) -> list[httpx.Response]:
    """One request to every generation route."""
    url = f"/api/generations/{generation_id}"
    return [
        await client.post(
            "/api/generations", json={"job_id": job_id}, headers=with_key(session, "another-key-1")
        ),
        await client.get("/api/generations", headers=session.headers),
        await client.get(url, headers=session.headers),
        await client.patch(url, json={"expected_revision": 1}, headers=session.headers),
        await client.post(f"{url}/validate", headers=session.headers),
        await client.post(
            f"{url}/items/some-item/regenerate", json={}, headers=with_key(session, "regen-key-01")
        ),
    ]


async def test_expired_token_is_rejected_on_every_generation_route(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    repos: Repositories,
    db: Database,
    fake_provider: FakeProvider,
) -> None:
    session = await create_session()
    await seed_profile(repos, session)
    job = await seed_job(repos, session)
    created = (await post_generation(client, session, job)).json()
    await db["sessions"].update_one(
        {"_id": session.session_id}, {"$set": {"expires_at": utc_now() - timedelta(seconds=1)}}
    )

    for response in await generation_requests(
        client, session, created["generation_id"], job.job_id
    ):
        assert_error(response, 401, "session_expired")
    assert fake_provider.calls["generate_documents"] == 1


async def test_revoked_or_missing_token_is_rejected_on_every_generation_route(
    client: httpx.AsyncClient, create_session: CreateSession, repos: Repositories
) -> None:
    session = await create_session()
    await seed_profile(repos, session)
    job = await seed_job(repos, session)
    created = (await post_generation(client, session, job)).json()
    await client.delete("/api/session", headers=session.headers)

    for response in await generation_requests(
        client, session, created["generation_id"], job.job_id
    ):
        assert_error(response, 401, "unauthorized")

    anonymous = SessionHandle("", {}, "", "", session.expires_at)
    for response in await generation_requests(
        client, anonymous, created["generation_id"], job.job_id
    ):
        assert_error(response, 401, "unauthorized")

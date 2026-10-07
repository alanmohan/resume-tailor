"""Failures seen from the client's side, through the real routes.

What a visitor needs when something goes wrong: an error that says whether
retrying makes sense, everything they had finished still in place, and a retry
that works. Also here: work still in flight when the visitor clears their data
must not write anything back, and a generation never outlives the window in
which its Idempotency-Key is reserved.
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import timedelta

import httpx
import pytest
from fastapi import FastAPI

from app.config import Settings
from app.db import Database
from app.providers.base import ProviderError, ProviderRateLimited, ProviderTimeout
from app.providers.fake.provider import FakeProvider
from app.repositories import Repositories
from app.repositories.generations import RUNNING_STALE_AFTER
from app.security import generate_token
from app.services import generation as generation_service
from app.services.indexing import INDEX_FAILED_MESSAGE
from tests.conftest import SessionHandle, client_for
from tests.helpers import assert_error
from tests.helpers_flow import (
    PROTECTED_REQUESTS,
    analysed_job,
    keyed,
    post_generation,
)
from tests.helpers_generation import (
    all_claims,
    owned_counts,
    seed_job,
    seed_profile,
    wait_for_call,
    with_key,
)
from tests.helpers_generation import post_generation as post_seeded_generation
from tests.integration.test_profile_flow import confirm, ingest, simple_sources

CreateSession = Callable[[], Awaitable[SessionHandle]]
MakeApp = Callable[[Settings], Awaitable[FastAPI]]
MakeSettings = Callable[..., Settings]

EMPTY = {"sources": 0, "profiles": 0, "evidence": 0, "jobs": 0, "generations": 0}
# Nothing listens on port 1, so every database operation fails to connect.
UNREACHABLE_URI = "mongodb://127.0.0.1:1/?directConnection=true"
DOCKER_BULLET = "Containerised twelve services with Docker for deployment."


# ---- Provider timeout and rate limit on every AI operation -------------------------


@pytest.mark.parametrize(
    ("error_type", "status", "code"),
    [
        (ProviderTimeout, 504, "provider_timeout"),
        (ProviderRateLimited, 503, "provider_rate_limited"),
    ],
)
async def test_provider_failure_keeps_finished_work_and_the_same_request_succeeds_on_retry(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    fake_provider: FakeProvider,
    error_type: type[ProviderError],
    status: int,
    code: str,
) -> None:
    headers = auth_headers

    def fail_once(operation: str) -> None:
        fake_provider.fail_next(operation, error_type("The AI provider did not answer in time."))

    def assert_retryable(response: httpx.Response) -> None:
        error = assert_error(response, status, code)
        assert error["retryable"] is True
        assert error["message"] == "The AI provider did not answer in time."

    # Ingestion: nothing is stored by the failed attempt.
    fail_once("extract_profile")
    failed = await client.post(
        "/api/profiles/ingest", json={"sources": simple_sources()}, headers=headers
    )
    assert_retryable(failed)
    assert_error(await client.get("/api/profile", headers=headers), 404, "not_found")
    profile = await ingest(client, headers, simple_sources())

    # Confirmation: the reviewed profile stays, the failure is shown on it.
    fail_once("embed")
    assert_retryable(await confirm(client, headers, profile["version"]))
    after_failure = (await client.get("/api/profile", headers=headers)).json()
    assert (after_failure["status"], after_failure["index_state"]) == ("draft", "failed")
    assert after_failure["index_error"] == INDEX_FAILED_MESSAGE
    assert after_failure["records"] == profile["records"]
    confirmed = await confirm(client, headers, profile["version"])
    assert confirmed.status_code == 200
    confirmed = confirmed.json()
    assert (confirmed["index_state"], confirmed["index_error"]) == ("indexed", None)

    # Job analysis: no half-analysed job is left behind.
    fail_once("analyze_job")
    failed = await client.post(
        "/api/jobs", json={"description": "Requirements\n- Python\n- SQL"}, headers=headers
    )
    assert_retryable(failed)
    assert (await client.get("/api/jobs", headers=headers)).json() == {"jobs": []}
    job = await analysed_job(client, headers, "injection_job")

    # Generation: the attempt is recorded as failed and the same key retries it.
    fail_once("generate_documents")
    assert_retryable(await post_generation(client, headers, job["job_id"], "recovery-key-0001"))
    listing = (await client.get("/api/generations", headers=headers)).json()["generations"]
    assert [item["status"] for item in listing] == ["failed"]
    failed_draft = (
        await client.get(f"/api/generations/{listing[0]['generation_id']}", headers=headers)
    ).json()
    assert failed_draft["error"]["code"] == code
    assert (failed_draft["resume"], failed_draft["cover_letter"]) == (None, None)
    assert (await client.get("/api/profile", headers=headers)).json() == confirmed
    assert (await client.get(f"/api/jobs/{job['job_id']}", headers=headers)).json() == job
    retried = await post_generation(client, headers, job["job_id"], "recovery-key-0001")
    assert retried.status_code == 201
    draft = retried.json()
    assert (draft["generation_id"], draft["status"], draft["error"]) == (
        listing[0]["generation_id"],
        "completed",
        None,
    )

    # Regeneration of one item: the draft is exactly as it was.
    item_id = draft["resume"]["experience"][0]["bullets"][0]["item_id"]
    url = f"/api/generations/{draft['generation_id']}/items/{item_id}/regenerate"
    fail_once("regenerate_item")
    assert_retryable(await client.post(url, headers=keyed(headers, "recovery-regen-0001")))
    unchanged = await client.get(f"/api/generations/{draft['generation_id']}", headers=headers)
    assert unchanged.json() == draft
    again = await client.post(url, headers=keyed(headers, "recovery-regen-0001"))
    assert again.status_code == 200
    assert again.json()["revision"] == draft["revision"] + 1


# ---- Database loss -----------------------------------------------------------------


async def test_routes_report_an_unreachable_database_as_retryable(
    make_app: MakeApp, make_settings: MakeSettings
) -> None:
    settings = make_settings(mongodb_uri=UNREACHABLE_URI, mongodb_server_selection_timeout_ms=100)
    application = await make_app(settings)
    # Well formed, so the request gets as far as looking the session up.
    headers = keyed({"Authorization": f"Bearer {generate_token()}"}, "database-down-0001")

    async with client_for(application) as client:
        assert (await client.get("/healthz")).status_code == 200
        # Every protected route looks the session up first, so a few of them
        # stand for all (each attempt waits for the connection timeout).
        requests = [("POST", "/api/sessions", None), *PROTECTED_REQUESTS[::4]]
        assert len(requests) == 6
        for method, url, body in requests:
            response = await client.request(method, url, json=body, headers=headers)
            error = assert_error(response, 503, "database_unavailable")
            assert error["retryable"] is True
            # No connection details in the answer.
            assert "127.0.0.1" not in response.text and "mongodb" not in response.text


# ---- Clearing data while work is in flight -----------------------------------------


async def test_clearing_data_during_a_slow_regeneration_leaves_nothing_behind(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    repos: Repositories,
    db: Database,
    fake_provider: FakeProvider,
) -> None:
    session = await create_session()
    await seed_profile(repos, session)
    job = await seed_job(repos, session)
    draft = (await post_seeded_generation(client, session, job)).json()
    bullet = next(claim for claim in all_claims(draft) if claim["text"] == DOCKER_BULLET)
    url = f"/api/generations/{draft['generation_id']}/items/{bullet['item_id']}/regenerate"
    fake_provider.set_delay("regenerate_item", 0.5)

    in_flight = asyncio.create_task(client.post(url, headers=with_key(session, "regen-clear-1")))
    await wait_for_call(fake_provider, "regenerate_item")  # now waiting for the provider
    cleared = await client.delete("/api/session", headers=session.headers)
    assert cleared.status_code == 200
    response = await in_flight

    assert_error(response, 401, "unauthorized")
    assert await owned_counts(db, session.owner_id) == EMPTY


async def test_clearing_data_during_a_slow_validation_leaves_nothing_behind(
    make_app: MakeApp, make_settings: MakeSettings
) -> None:
    # With the optional verifier on, validation waits for a provider call too.
    application = await make_app(make_settings(enable_semantic_verifier=True))
    provider = application.state.provider
    db = application.state.mongo.db
    repos = Repositories(db)

    async with client_for(application) as client:
        token = (await client.post("/api/sessions")).json()["token"]
        stored = await db["sessions"].find_one({})
        session = SessionHandle(
            token=token,
            headers={"Authorization": f"Bearer {token}"},
            owner_id=stored["owner_id"],
            session_id=stored["_id"],
            expires_at=stored["expires_at"],
        )
        await seed_profile(repos, session)
        job = await seed_job(repos, session)
        draft = (await post_seeded_generation(client, session, job)).json()
        bullet = next(claim for claim in all_claims(draft) if claim["text"] == DOCKER_BULLET)
        url = f"/api/generations/{draft['generation_id']}"
        edited = await client.patch(
            url,
            json={
                "expected_revision": draft["revision"],
                "edits": [{"item_id": bullet["item_id"], "text": "Containerised twelve services."}],
            },
            headers=session.headers,
        )
        assert edited.status_code == 200, edited.text
        provider.reset()
        provider.set_delay("verify_claims", 0.5)

        in_flight = asyncio.create_task(client.post(f"{url}/validate", headers=session.headers))
        await wait_for_call(provider, "verify_claims")
        cleared = await client.delete("/api/session", headers=session.headers)
        assert cleared.status_code == 200
        response = await in_flight

    assert_error(response, 401, "unauthorized")
    assert await owned_counts(db, session.owner_id) == EMPTY


# ---- A generation never outlives its reservation -----------------------------------


def test_generation_deadline_ends_before_a_retry_could_take_the_run_over() -> None:
    assert timedelta(0) < generation_service.GENERATION_DEADLINE < RUNNING_STALE_AFTER


async def test_generation_that_runs_too_long_fails_as_a_timeout_and_can_be_retried(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    repos: Repositories,
    db: Database,
    fake_provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = await create_session()
    await seed_profile(repos, session)
    job = await seed_job(repos, session)
    monkeypatch.setattr(generation_service, "GENERATION_DEADLINE", timedelta(seconds=0.05))
    fake_provider.set_delay("generate_documents", 0.5)

    timed_out = await post_seeded_generation(client, session, job, key="deadline-key-1")

    error = assert_error(timed_out, 504, "provider_timeout")
    assert error["retryable"] is True
    assert error["message"] == generation_service.GENERATION_TIMEOUT_MESSAGE
    stored = await db["generations"].find_one({"owner_id": session.owner_id})
    assert (stored["status"], stored["error"]["code"]) == ("failed", "provider_timeout")
    assert stored["resume"] is None

    fake_provider.set_delay("generate_documents", None)
    retried = await post_seeded_generation(client, session, job, key="deadline-key-1")
    assert retried.status_code == 201
    assert retried.json()["generation_id"] == stored["_id"]
    assert fake_provider.calls["generate_documents"] == 2

"""The job endpoints: analysis of a job description, the requirement review,
versioning, input limits, provider failures, hidden instructions, isolation."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from app.config import Settings
from app.db import Database
from app.providers.base import ProviderError, ProviderRateLimited, ProviderTimeout
from app.providers.fake.provider import FakeProvider
from tests.conftest import SessionHandle, client_for
from tests.helpers import assert_error, parse_iso_z
from tests.integration.test_profile_flow import fixture_text

CreateSession = Callable[[], Awaitable[SessionHandle]]
MakeApp = Callable[[Settings], Awaitable[FastAPI]]
MakeSettings = Callable[..., Settings]

SHORT_POSTING = (
    "About the role\nGlobex needs a platform engineer.\n\n"
    "Requirements\n- Strong Python skills\n- Experience with Docker\n\n"
    "Nice to have\n- Experience with AWS\n"
)


def job_fixture(slug: str) -> dict[str, Any]:
    return json.loads(fixture_text(f"jobs/synthetic/{slug}.json"))


async def create_job(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    description: str = SHORT_POSTING,
    **extra: Any,
) -> dict[str, Any]:
    response = await client.post(
        "/api/jobs", json={"description": description, **extra}, headers=headers
    )
    assert response.status_code == 201, response.text
    return response.json()


def review_body(job: dict[str, Any], **extra: Any) -> dict[str, Any]:
    """The PATCH body that saves the job's requirements exactly as they are."""
    requirements = [
        {name: item[name] for name in ("requirement_id", "text", "category", "importance")}
        for item in job["requirements"]
    ]
    return {"expected_version": job["version"], "requirements": requirements} | extra


# ---- Analysis ----------------------------------------------------------------------


async def test_job_is_analysed_into_requirements_with_spans_in_the_original_text(
    client: httpx.AsyncClient, create_session: CreateSession, db: Database
) -> None:
    session = await create_session()
    fixture = job_fixture("close_fit")
    # Windows line endings and indented bullets, as text pasted from a web page.
    description = fixture["description"].replace("\n", "\r\n").replace("- ", "   -   ")

    job = await create_job(
        client,
        session.headers,
        description,
        title=f"  {fixture['title']}  ",
        company=fixture["company"],
    )

    assert set(job) == {
        "job_id",
        "version",
        "company",
        "title",
        "description",
        "role_summary",
        "requirements",
        "created_at",
        "updated_at",
        "expires_at",
    }
    assert job["version"] == 1
    assert (job["title"], job["company"]) == (fixture["title"], fixture["company"])
    # The description is stored and returned exactly as submitted.
    assert job["description"] == description
    assert job["role_summary"].startswith("Fernhollow AI builds search")
    assert parse_iso_z(job["expires_at"]) == session.expires_at

    requirements = job["requirements"]
    assert len(requirements) == 16
    assert len({item["requirement_id"] for item in requirements}) == 16
    for item in requirements:
        assert set(item) == {
            "requirement_id",
            "text",
            "category",
            "importance",
            "inferred",
            "keywords",
            "source_span",
            "user_edited",
        }
        span = item["source_span"]
        assert description[span["start"] : span["end"]] == span["excerpt"] == item["text"]
        assert item["user_edited"] is False
        assert item["keywords"] == [keyword.lower() for keyword in item["keywords"]]

    explicit = [item for item in requirements if not item["inferred"]]
    assert [item["importance"] for item in explicit] == ["required"] * 7 + ["preferred"] * 4
    assert {item["category"] for item in requirements if item["inferred"]} == {"responsibility"}
    pytorch = next(item for item in explicit if item["text"] == "Hands-on experience with PyTorch")
    assert (pytorch["category"], pytorch["keywords"]) == ("skill", ["pytorch"])

    stored = await db["jobs"].find_one({"_id": job["job_id"]})
    assert stored["owner_id"] == session.owner_id
    assert stored["expires_at"] == session.expires_at


async def test_title_and_company_are_optional(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    job = await create_job(client, auth_headers, SHORT_POSTING, company="   ")
    assert (job["title"], job["company"]) == (None, None)
    assert [item["text"] for item in job["requirements"]] == [
        "Strong Python skills",
        "Experience with Docker",
        "Experience with AWS",
    ]


async def test_requirements_are_capped_at_the_configured_maximum(
    make_app: MakeApp, make_settings: MakeSettings
) -> None:
    application = await make_app(make_settings(max_requirements=6))
    async with client_for(application) as client:
        token = (await client.post("/api/sessions")).json()["token"]
        job = await create_job(
            client, {"Authorization": f"Bearer {token}"}, job_fixture("close_fit")["description"]
        )
    # Explicit required qualifications are kept before duties and preferences.
    assert len(job["requirements"]) == 6
    assert all(
        item["importance"] == "required" and not item["inferred"] for item in job["requirements"]
    )


# ---- Validation --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({}, "description"),
        ({"description": ""}, "description"),
        ({"description": " \n\t "}, "description"),
        ({"description": "A job.", "title": "x" * 201}, "title"),
        ({"description": "A job.", "company": 7}, "company"),
    ],
)
async def test_invalid_job_input_is_rejected_with_the_field_at_fault(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    db: Database,
    fake_provider: FakeProvider,
    body: dict[str, Any],
    field: str,
) -> None:
    session = await create_session()

    error = assert_error(
        await client.post("/api/jobs", json=body, headers=session.headers), 422, "validation_error"
    )

    assert field in [item["field"] for item in error["field_errors"]]
    assert fake_provider.calls["analyze_job"] == 0
    assert await db["jobs"].count_documents({"owner_id": session.owner_id}) == 0
    assert (await db["sessions"].find_one({"_id": session.session_id}))["quota"][
        "job_analysis"
    ] == 0


async def test_empty_description_message_says_what_is_wrong(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    error = assert_error(
        await client.post("/api/jobs", json={"description": "  "}, headers=auth_headers),
        422,
        "validation_error",
    )
    assert error["field_errors"] == [
        {"field": "description", "message": "Job description must not be empty"}
    ]


async def test_description_over_the_limit_is_rejected_with_both_sizes(
    make_app: MakeApp, make_settings: MakeSettings
) -> None:
    application = await make_app(make_settings(max_job_chars=200))
    async with client_for(application) as client:
        token = (await client.post("/api/sessions")).json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        too_long = await client.post("/api/jobs", json={"description": "x" * 201}, headers=headers)

        error = assert_error(too_long, 413, "input_too_large")
        assert "201 characters" in error["message"] and "limit is 200" in error["message"]
        assert error["field_errors"][0]["field"] == "description"
        assert application.state.provider.calls["analyze_job"] == 0
        at_limit = await client.post("/api/jobs", json={"description": "x" * 200}, headers=headers)
        assert at_limit.status_code == 201


# ---- Reading -----------------------------------------------------------------------


async def test_jobs_are_listed_newest_first_and_can_be_read_back(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    assert (await client.get("/api/jobs", headers=auth_headers)).json() == {"jobs": []}
    first = await create_job(client, auth_headers, title="First", company="Globex")
    await asyncio.sleep(0.01)  # created_at has millisecond precision
    second = await create_job(client, auth_headers, title="Second")

    listing = (await client.get("/api/jobs", headers=auth_headers)).json()

    assert [item["job_id"] for item in listing["jobs"]] == [second["job_id"], first["job_id"]]
    assert listing["jobs"][1] == {
        "job_id": first["job_id"],
        "title": "First",
        "company": "Globex",
        "version": 1,
        "requirement_count": 3,
        "created_at": first["created_at"],
        "updated_at": first["updated_at"],
    }
    read = await client.get(f"/api/jobs/{first['job_id']}", headers=auth_headers)
    assert read.status_code == 200
    assert read.json() == first


# ---- Review ------------------------------------------------------------------------


async def test_review_saves_edits_additions_and_removals_as_a_new_version(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    job = await create_job(client, auth_headers, title="Platform Engineer", company="Globex")
    python, docker, aws = job["requirements"]
    body = review_body(job, title="Senior Platform Engineer")
    body["requirements"] = [
        body["requirements"][0] | {"text": "Strong Python and SQL skills"},
        body["requirements"][2] | {"importance": "required"},
        {
            "requirement_id": None,
            "text": "Kubernetes",
            "category": "skill",
            "importance": "preferred",
        },
    ]

    response = await client.patch(f"/api/jobs/{job['job_id']}", json=body, headers=auth_headers)

    assert response.status_code == 200
    updated = response.json()
    assert updated["version"] == 2
    assert (updated["title"], updated["company"]) == ("Senior Platform Engineer", "Globex")
    assert updated["description"] == job["description"]
    edited, promoted, added = updated["requirements"]
    assert edited == python | {"text": "Strong Python and SQL skills", "user_edited": True}
    assert promoted == aws | {"importance": "required", "user_edited": True}
    assert added["text"] == "Kubernetes"
    assert (added["user_edited"], added["inferred"], added["source_span"]) == (True, False, None)
    assert added["requirement_id"] not in {python["requirement_id"], docker["requirement_id"]}
    assert (await client.get(f"/api/jobs/{job['job_id']}", headers=auth_headers)).json() == updated
    listing = (await client.get("/api/jobs", headers=auth_headers)).json()
    assert (listing["jobs"][0]["version"], listing["jobs"][0]["requirement_count"]) == (2, 3)


async def test_saving_unchanged_requirements_keeps_the_version(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    job = await create_job(client, auth_headers, title="Platform Engineer")
    response = await client.patch(
        f"/api/jobs/{job['job_id']}", json=review_body(job), headers=auth_headers
    )
    assert response.status_code == 200
    assert response.json() == job


async def test_stale_expected_version_is_a_conflict(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    job = await create_job(client, auth_headers)
    url = f"/api/jobs/{job['job_id']}"
    saved = (
        await client.patch(url, json=review_body(job, title="Renamed"), headers=auth_headers)
    ).json()

    stale = await client.patch(url, json=review_body(job, title="Too late"), headers=auth_headers)

    assert assert_error(stale, 409, "version_conflict")["details"] == {"current_version": 2}
    assert (await client.get(url, headers=auth_headers)).json() == saved


async def test_invalid_reviews_are_rejected(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    job = await create_job(client, auth_headers)
    url = f"/api/jobs/{job['job_id']}"
    unknown = review_body(job)
    unknown["requirements"][0]["requirement_id"] = "f" * 32
    blank = review_body(job)
    blank["requirements"][1]["text"] = "  "
    too_long = review_body(job)
    too_long["requirements"][1]["text"] = "x" * 501
    too_many = review_body(job)
    too_many["requirements"] = [
        {"text": f"Requirement {number}", "category": "other", "importance": "preferred"}
        for number in range(26)
    ]
    cases = [
        (unknown, "requirements.0.requirement_id"),
        (blank, "requirements.1.text"),
        (too_long, "requirements.1.text"),
        (too_many, "requirements"),
        ({"requirements": []}, "expected_version"),
    ]
    for body, field in cases:
        error = assert_error(
            await client.patch(url, json=body, headers=auth_headers), 422, "validation_error"
        )
        assert field in [item["field"] for item in error["field_errors"]]
    assert (await client.get(url, headers=auth_headers)).json() == job


# ---- Failures ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("failure", "status", "code"),
    [
        (ProviderTimeout("The AI provider took too long to respond."), 504, "provider_timeout"),
        (ProviderRateLimited("The AI provider is rate limiting."), 503, "provider_rate_limited"),
    ],
)
async def test_provider_failure_maps_to_its_code_and_keeps_existing_jobs(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    fake_provider: FakeProvider,
    failure: ProviderError,
    status: int,
    code: str,
) -> None:
    existing = await create_job(client, auth_headers, title="Kept")
    fake_provider.fail_next("analyze_job", failure)

    failed = await client.post(
        "/api/jobs", json={"description": SHORT_POSTING}, headers=auth_headers
    )

    assert assert_error(failed, status, code)["retryable"] is True
    listing = (await client.get("/api/jobs", headers=auth_headers)).json()
    assert [item["job_id"] for item in listing["jobs"]] == [existing["job_id"]]
    # Retrying the same request works once the provider recovers.
    assert (await create_job(client, auth_headers))["version"] == 1


async def test_job_analysis_quota_is_enforced_per_session(
    make_app: MakeApp, make_settings: MakeSettings
) -> None:
    application = await make_app(make_settings(quota_job_analysis=1))
    async with client_for(application) as client:
        token = (await client.post("/api/sessions")).json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        await create_job(client, headers)

        second = await client.post(
            "/api/jobs", json={"description": SHORT_POSTING}, headers=headers
        )

        error = assert_error(second, 429, "quota_exceeded")
        assert error["details"] == {"operation": "job_analysis", "limit": 1}
        assert application.state.provider.calls["analyze_job"] == 1


async def test_clearing_data_while_a_job_is_analysed_leaves_nothing_behind(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    db: Database,
    fake_provider: FakeProvider,
) -> None:
    session = await create_session()
    fake_provider.set_delay("analyze_job", 0.25)

    analysing = asyncio.create_task(
        client.post("/api/jobs", json={"description": SHORT_POSTING}, headers=session.headers)
    )
    await asyncio.sleep(0.05)  # the request is now waiting for the provider
    assert (await client.delete("/api/session", headers=session.headers)).status_code == 200

    assert_error(await analysing, 401, "unauthorized")
    assert await db["jobs"].count_documents({"owner_id": session.owner_id}) == 0


# ---- Hidden instructions -----------------------------------------------------------


async def test_instructions_hidden_in_a_posting_change_nothing_but_the_stored_text(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    fixture = job_fixture("injection_job")
    injected_text = fixture["injection"]["injected_text"]
    clean_description = fixture["description"].replace(injected_text + "\n\n", "")

    injected = await create_job(
        client, auth_headers, fixture["description"], title=fixture["title"]
    )
    clean = await create_job(client, auth_headers, clean_description, title=fixture["title"])

    # The posting is stored as submitted; nothing derived from it repeats the injection.
    assert injected["description"] == fixture["description"]
    derived = json.dumps({key: value for key, value in injected.items() if key != "description"})
    for forbidden in [fixture["injection"]["canary"], *fixture["expected"]["must_not_claim"]]:
        assert forbidden not in derived
    assert "system prompt" not in derived.lower()

    def facts(job: dict[str, Any]) -> list[tuple]:
        return [
            (item["text"], item["category"], item["importance"], item["inferred"], item["keywords"])
            for item in job["requirements"]
        ]

    assert facts(injected) == facts(clean)
    assert injected["role_summary"] == clean["role_summary"]
    required = {
        kw
        for item in injected["requirements"]
        if item["importance"] == "required"
        for kw in item["keywords"]
    }
    assert {"python", "rest api", "postgresql", "docker"} <= required


# ---- Isolation ---------------------------------------------------------------------


async def test_two_sessions_cannot_see_or_change_each_others_jobs(
    client: httpx.AsyncClient, create_session: CreateSession
) -> None:
    alice, bob = await create_session(), await create_session()
    job = await create_job(client, alice.headers, title="Alice's job")
    url = f"/api/jobs/{job['job_id']}"

    assert (await client.get("/api/jobs", headers=bob.headers)).json() == {"jobs": []}
    foreign = await client.get(url, headers=bob.headers)
    missing = await client.get(f"/api/jobs/{'0' * 32}", headers=bob.headers)
    assert assert_error(foreign, 404, "not_found") | {"request_id": ""} == (
        assert_error(missing, 404, "not_found") | {"request_id": ""}
    )
    assert_error(
        await client.patch(url, json=review_body(job, title="Hijacked"), headers=bob.headers),
        404,
        "not_found",
    )

    await client.delete("/api/session", headers=bob.headers)
    assert (await client.get(url, headers=alice.headers)).json() == job

"""POST /api/profiles/ingest: input validation, storage of the original text,
re-ingestion, provider failures, quotas, hidden instructions and the race with
"Clear my data"."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from app.config import Settings
from app.db import OWNED_COLLECTIONS, Database
from app.providers.base import (
    ProviderError,
    ProviderInvalidOutput,
    ProviderRateLimited,
    ProviderTimeout,
)
from app.providers.fake.provider import FakeProvider
from app.services.textutil import content_hash
from tests.conftest import SessionHandle, client_for
from tests.helpers import assert_error
from tests.integration.test_profile_flow import (
    confirmed_profile,
    evidence_documents,
    fixture_text,
    ingest,
    patch_body,
    sample_sources,
    simple_sources,
    source,
)

CreateSession = Callable[[], Awaitable[SessionHandle]]
MakeApp = Callable[[Settings], Awaitable[FastAPI]]
MakeSettings = Callable[..., Settings]


async def post_ingest(
    client: httpx.AsyncClient, headers: dict[str, str], sources: list[dict[str, Any]]
) -> httpx.Response:
    return await client.post("/api/profiles/ingest", json={"sources": sources}, headers=headers)


async def owned_counts(db: Database, owner_id: str) -> dict[str, int]:
    return {
        name: await db[name].count_documents({"owner_id": owner_id}) for name in OWNED_COLLECTIONS
    }


async def session_headers(client: httpx.AsyncClient) -> dict[str, str]:
    """A new session on whichever app ``client`` talks to."""
    token = (await client.post("/api/sessions")).json()["token"]
    return {"Authorization": f"Bearer {token}"}


# ---- Validation --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sources", "field"),
    [
        ([], "sources"),
        ([source("Resume", "resume", "   \n\t ")], "sources.0.text"),
        ([source("Resume", "resume", "")], "sources.0.text"),
        ([source("", "resume", "Some text")], "sources.0.label"),
        ([source("x" * 81, "resume", "Some text")], "sources.0.label"),
        ([source("Resume", "pdf", "Some text")], "sources.0.source_type"),
        ([source("Resume", "resume", "Some text"), {"label": "Notes"}], "sources.1.source_type"),
    ],
)
async def test_invalid_sources_are_rejected_with_the_field_at_fault(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    db: Database,
    fake_provider: FakeProvider,
    sources: list[dict[str, Any]],
    field: str,
) -> None:
    session = await create_session()

    error = assert_error(
        await post_ingest(client, session.headers, sources), 422, "validation_error"
    )

    assert field in [item["field"] for item in error["field_errors"]]
    # Rejected input costs nothing: no provider call, no quota, nothing stored.
    assert fake_provider.calls["extract_profile"] == 0
    assert (await db["sessions"].find_one({"_id": session.session_id}))["quota"]["ingest"] == 0
    assert sum((await owned_counts(db, session.owner_id)).values()) == 0


async def test_blank_source_message_says_what_is_wrong(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    sources = [source("Resume", "resume", "Some text"), source("Notes", "notes", "  ")]
    error = assert_error(await post_ingest(client, auth_headers, sources), 422, "validation_error")
    assert error["field_errors"] == [
        {"field": "sources.1.text", "message": "Source text must not be empty"}
    ]


async def test_too_many_sources_are_rejected(
    client: httpx.AsyncClient, auth_headers: dict[str, str], fake_provider: FakeProvider
) -> None:
    sources = [source(f"Source {number}", "notes", "Some text") for number in range(6)]

    error = assert_error(await post_ingest(client, auth_headers, sources), 422, "validation_error")

    assert error["message"] == "At most 5 sources can be submitted at once; 6 were sent."
    assert error["field_errors"][0]["field"] == "sources"
    assert fake_provider.calls["extract_profile"] == 0


async def test_profile_text_over_the_limit_is_rejected_with_both_sizes(
    make_app: MakeApp, make_settings: MakeSettings
) -> None:
    application = await make_app(make_settings(max_profile_chars=1000))
    sources = [source("Resume", "resume", "a" * 600), source("Notes", "notes", "b" * 601)]
    async with client_for(application) as client:
        headers = await session_headers(client)

        error = assert_error(await post_ingest(client, headers, sources), 413, "input_too_large")

        assert "1,201 characters" in error["message"]
        assert "limit is 1,000" in error["message"]
        assert error["field_errors"][0]["field"] == "sources"
        assert application.state.provider.calls["extract_profile"] == 0
        # Exactly at the limit is accepted.
        sources[1]["text"] = "b" * 400
        assert (await post_ingest(client, headers, sources)).status_code == 200


# ---- What is stored ----------------------------------------------------------------


async def test_original_text_is_stored_untouched_and_references_point_into_it(
    client: httpx.AsyncClient, create_session: CreateSession, db: Database
) -> None:
    session = await create_session()
    # Windows line endings, doubled spaces and a non-breaking space.
    original = (
        fixture_text("profiles/sample_resume.txt")
        .replace("\n", "\r\n")
        .replace(" - ", "  -  ")
        .replace("Fine-tuned a", "Fine-tuned a")
    )

    profile = await ingest(client, session.headers, [source("My CV", "resume", original)])

    stored = await db["sources"].find_one({"owner_id": session.owner_id})
    assert stored["text"] == original
    assert stored["content_hash"] == content_hash(original)
    assert stored["char_count"] == len(original) == profile["sources"][0]["char_count"]
    assert stored["expires_at"] == session.expires_at
    assert stored["profile_id"] == profile["profile_id"]

    references = [
        ref
        for record in profile["records"]
        for ref in [record["source_ref"], *(bullet["source_ref"] for bullet in record["bullets"])]
    ]
    assert len(references) > 25
    for ref in references:
        assert ref["source_id"] == stored["_id"]
        assert ref["source_label"] == "My CV"
        assert original[ref["start"] : ref["end"]] == ref["excerpt"]
    role = profile["records"][0]
    assert role["source_ref"]["excerpt"] == (
        "Machine Learning Engineer  -  Brightloom Labs (Aug 2024  -  Present)"
    )
    assert role["start_date"] == "Aug 2024"
    assert not [record for record in profile["records"] if record["needs_review"]]


async def test_sample_profile_is_extracted_with_conflict_and_review_flag(
    client: httpx.AsyncClient, create_session: CreateSession, db: Database
) -> None:
    session = await create_session()
    expected = json.loads(fixture_text("profiles/sample_expected.json"))

    profile = await ingest(client, session.headers)

    assert profile["contact"] == {
        "name": expected["contact"]["name"],
        "email": expected["contact"]["email"],
        "phone": expected["contact"]["phone"],
        "location": expected["contact"]["location"],
        "links": [expected["contact"]["link"]],
    }
    roles = [record for record in profile["records"] if record["category"] == "employment"]
    assert [(role["title"], role["organization"]) for role in roles[:3]] == [
        (role["title"], role["employer"]) for role in expected["roles"]
    ]
    # One record per role although it appears in up to three sources.
    assert len(roles) == 4
    assert {bullet["source_ref"]["source_label"] for bullet in roles[0]["bullets"]} == {
        "Resume",
        "LinkedIn",
        "Notes",
    }

    conflict = profile["conflicts"][0]
    assert len(profile["conflicts"]) == 1
    assert conflict["resolution"] == "unresolved"
    assert conflict["record_ids"] == [roles[1]["record_id"]]
    assert [value["value"] for value in conflict["values"]] == ["Jul 2022", "Jun 2022"]
    assert [value["source_ref"]["source_label"] for value in conflict["values"]] == [
        "Resume",
        "LinkedIn",
    ]

    undated = roles[3]
    assert undated["title"] == expected["ambiguous_items"][0]["title"]
    assert (undated["start_date"], undated["end_date"]) == (None, None)
    assert undated["needs_review"] is True
    assert profile["review_summary"] == {"needs_review_count": 1, "unresolved_conflict_count": 1}

    document = await db["profiles"].find_one({"owner_id": session.owner_id})
    assert document["_id"] == profile["profile_id"]
    assert document["expires_at"] == session.expires_at


async def test_ingesting_again_replaces_sources_and_draft_but_keeps_the_profile(
    client: httpx.AsyncClient, create_session: CreateSession, db: Database
) -> None:
    session = await create_session()
    first = await confirmed_profile(client, session.headers, simple_sources())
    first_source_ids = {item["source_id"] for item in first["sources"]}
    first_evidence = await evidence_documents(db, session.owner_id, first["version"])

    second = await ingest(client, session.headers)

    assert second["profile_id"] == first["profile_id"]
    assert second["version"] == first["version"] + 1
    assert second["created_at"] == first["created_at"]
    assert (second["status"], second["index_state"]) == ("draft", "not_indexed")
    assert second["indexed_version"] is None
    assert second["index_progress"] == {"total": 0, "embedded": 0}
    assert [item["revision"] for item in second["sources"]] == [2, 2, 2]
    assert not first_source_ids & {item["source_id"] for item in second["sources"]}
    assert await db["sources"].count_documents({"owner_id": session.owner_id}) == 3
    assert await db["profiles"].count_documents({"owner_id": session.owner_id}) == 1
    assert "Riley Park" not in json.dumps(second)
    # Evidence of the confirmed version stays, so drafts made from it keep their citations.
    assert await evidence_documents(db, session.owner_id, first["version"]) == first_evidence


# ---- Failures ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("failure", "status", "code"),
    [
        (ProviderTimeout("The AI provider took too long to respond."), 504, "provider_timeout"),
        (ProviderRateLimited("The AI provider is rate limiting."), 503, "provider_rate_limited"),
        (ProviderInvalidOutput("The AI response was cut off."), 502, "provider_invalid_output"),
    ],
)
async def test_provider_failure_maps_to_its_code_and_keeps_stored_work(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    db: Database,
    fake_provider: FakeProvider,
    failure: ProviderError,
    status: int,
    code: str,
) -> None:
    session = await create_session()
    before = await ingest(client, session.headers, simple_sources())
    fake_provider.fail_next("extract_profile", failure)

    error = assert_error(await post_ingest(client, session.headers, sample_sources()), status, code)

    assert error["retryable"] is True
    assert error["message"] == failure.message
    # The earlier profile and its sources are exactly as they were.
    assert (await client.get("/api/profile", headers=session.headers)).json() == before
    assert await db["sources"].count_documents({"owner_id": session.owner_id}) == 1
    # The same request succeeds when it is retried.
    assert (await post_ingest(client, session.headers, sample_sources())).status_code == 200


async def test_first_ingest_that_fails_stores_nothing(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    db: Database,
    fake_provider: FakeProvider,
) -> None:
    session = await create_session()
    fake_provider.fail_next("extract_profile", ProviderTimeout("too slow"))

    assert_error(
        await post_ingest(client, session.headers, sample_sources()), 504, "provider_timeout"
    )

    assert sum((await owned_counts(db, session.owner_id)).values()) == 0
    assert_error(await client.get("/api/profile", headers=session.headers), 404, "not_found")


async def test_ingest_quota_is_enforced_per_session(
    make_app: MakeApp, make_settings: MakeSettings
) -> None:
    application = await make_app(make_settings(quota_ingest=1))
    async with client_for(application) as client:
        headers = await session_headers(client)
        assert (await post_ingest(client, headers, simple_sources())).status_code == 200

        error = assert_error(
            await post_ingest(client, headers, simple_sources()), 429, "quota_exceeded"
        )

        assert error["details"] == {"operation": "ingest", "limit": 1}
        assert application.state.provider.calls["extract_profile"] == 1
        # Another visitor has their own quota.
        other = await session_headers(client)
        assert (await post_ingest(client, other, simple_sources())).status_code == 200


async def test_global_ai_call_cap_stops_ingestion_before_the_provider_is_called(
    make_app: MakeApp, make_settings: MakeSettings
) -> None:
    application = await make_app(make_settings(global_daily_ai_call_limit=0))
    async with client_for(application) as client:
        headers = await session_headers(client)
        error = assert_error(
            await post_ingest(client, headers, simple_sources()), 429, "quota_exceeded"
        )
        assert error["details"] == {"scope": "global_daily"}
        assert application.state.provider.calls["extract_profile"] == 0


# ---- Hidden instructions -----------------------------------------------------------


async def test_instruction_hidden_in_a_resume_is_stored_as_text_and_never_extracted(
    client: httpx.AsyncClient, create_session: CreateSession, db: Database
) -> None:
    expected = json.loads(fixture_text("profiles/injection_expected.json"))
    resume = fixture_text("profiles/injection_resume.txt")
    clean_resume = resume.replace(expected["injected_line"] + "\n", "")
    injected_session, clean_session = await create_session(), await create_session()

    injected = await ingest(client, injected_session.headers, [source("Resume", "resume", resume)])
    clean = await ingest(client, clean_session.headers, [source("Resume", "resume", clean_resume)])

    response_text = json.dumps(injected)
    for forbidden in expected["must_not_claim"]:
        assert forbidden not in response_text
    assert not [record for record in injected["records"] if record["needs_review"]]

    def facts(profile: dict[str, Any]) -> list[tuple]:
        return [
            (
                record["category"],
                record["title"],
                record["organization"],
                record["start_date"],
                record["end_date"],
                [bullet["text"] for bullet in record["bullets"]],
                record["skills"],
            )
            for record in profile["records"]
        ]

    # The injected line changed nothing about what was extracted.
    assert facts(injected) == facts(clean)
    assert injected["contact"] == clean["contact"]
    assert [r["title"] for r in injected["records"] if r["category"] == "employment"] == [
        role["title"] for role in expected["roles"]
    ]
    # The original text, injected line included, is stored only as source text.
    stored = await db["sources"].find_one({"owner_id": injected_session.owner_id})
    assert expected["injected_line"] in stored["text"]


# ---- Concurrency -------------------------------------------------------------------


async def test_clearing_data_while_ingesting_leaves_nothing_behind(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    db: Database,
    fake_provider: FakeProvider,
) -> None:
    session = await create_session()
    fake_provider.set_delay("extract_profile", 0.25)

    ingesting = asyncio.create_task(post_ingest(client, session.headers, sample_sources()))
    await asyncio.sleep(0.05)  # the request is now waiting for the provider
    assert not ingesting.done()
    cleared = await client.delete("/api/session", headers=session.headers)
    assert cleared.status_code == 200

    assert_error(await ingesting, 401, "unauthorized")
    assert fake_provider.calls["extract_profile"] == 1
    assert sum((await owned_counts(db, session.owner_id)).values()) == 0


async def test_edit_during_a_slow_ingest_wins_and_nothing_is_replaced(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    db: Database,
    fake_provider: FakeProvider,
) -> None:
    session = await create_session()
    profile = await ingest(client, session.headers, simple_sources())
    source_ids = {item["source_id"] for item in profile["sources"]}
    fake_provider.set_delay("extract_profile", 0.25)

    ingesting = asyncio.create_task(post_ingest(client, session.headers, sample_sources()))
    await asyncio.sleep(0.05)
    body = patch_body(profile)
    body["contact"]["phone"] = "555-0100"
    edited = await client.patch("/api/profile", json=body, headers=session.headers)
    assert edited.status_code == 200

    assert_error(await ingesting, 409, "version_conflict")
    current = (await client.get("/api/profile", headers=session.headers)).json()
    assert current == edited.json()
    assert {item["source_id"] for item in current["sources"]} == source_ids

"""Review fixes through the HTTP API: profile notices (EM-03, EM-08), contact
conflicts and the confirm gate (SPEC-03), and the prose-heavy fictional
fixture (EM-13). The provider is the deterministic fake; all data is invented."""

from typing import Any

import httpx

from tests.helpers import assert_error
from tests.integration.test_profile_flow import (
    confirm,
    fixture_text,
    ingest,
    patch_body,
    resolve_conflicts,
    sample_sources,
    source,
)

UNCAPTURED = "source_text_not_captured"
COMPLETENESS = ["missing_name", "missing_contact_details", "missing_education"]

# The rule-based fake reads bullets and the text above them; it skips a plain
# line that follows the bullets, which stands in here for text a model missed.
SKIPPED_LINE = "Afterwards I trained the two analysts who took the pipelines over."
RESUME_WITH_A_SKIPPED_LINE = (
    "Riley Park\nriley.park@example.com\n\n"
    "Experience\n"
    "Data Engineer - Northwind Labs (Jan 2020 - Mar 2021)\n"
    "- Built Python pipelines for 12 analysts\n"
    f"{SKIPPED_LINE}\n\n"
    "Education\n"
    "B.S. in Statistics - Lakeshore University (2015 - 2019)\n"
)


def prose_sources() -> list[dict[str, str]]:
    return [source("Career notes", "notes", fixture_text("profiles/prose_profile.txt"))]


def codes(profile: dict[str, Any]) -> list[str]:
    return [notice["code"] for notice in profile["notices"]]


async def test_text_the_extraction_skipped_is_reported_without_blocking_confirmation(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    profile = await ingest(
        client, auth_headers, [source("Resume", "resume", RESUME_WITH_A_SKIPPED_LINE)]
    )

    assert profile["notices"] == [
        {
            "code": UNCAPTURED,
            "message": f'1 line of Resume was not captured: "{SKIPPED_LINE}". '
            "Add anything that matters to a record.",
        }
    ]
    # A notice is not a review flag and not a conflict.
    assert profile["review_summary"] == {"needs_review_count": 0, "unresolved_conflict_count": 0}

    reloaded = await client.get("/api/profile", headers=auth_headers)
    assert reloaded.json()["notices"] == profile["notices"]

    confirmed = await confirm(client, auth_headers, profile["version"])
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "confirmed"
    assert confirmed.json()["notices"] == profile["notices"]


async def test_profile_without_name_contact_or_education_is_confirmed_with_notices(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    profile = await ingest(client, auth_headers, prose_sources())

    assert codes(profile) == COMPLETENESS
    assert all(set(notice) == {"code", "message"} for notice in profile["notices"])
    assert profile["contact"] == {
        "name": None,
        "email": None,
        "phone": None,
        "location": None,
        "links": [],
    }
    assert profile["review_summary"] == {"needs_review_count": 0, "unresolved_conflict_count": 0}

    confirmed = await confirm(client, auth_headers, profile["version"])
    assert confirmed.status_code == 200, confirmed.text
    assert (confirmed.json()["status"], confirmed.json()["index_state"]) == ("confirmed", "indexed")
    assert codes(confirmed.json()) == COMPLETENESS


async def test_adding_the_missing_details_removes_their_notices(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    profile = await ingest(client, auth_headers, prose_sources())
    degree = {
        "record_id": None,
        "category": "education",
        "title": "B.S. in Hydrology",
        "organization": "Lakeshore University",
        "bullets": [],
        "skills": [],
    }
    body = patch_body(profile)
    body["contact"] = profile["contact"] | {"name": "Riley Park"}
    named = await client.patch("/api/profile", json=body, headers=auth_headers)
    assert named.status_code == 200, named.text
    assert codes(named.json()) == COMPLETENESS[1:]

    body = patch_body(named.json())
    body["contact"] = named.json()["contact"] | {"email": "riley.park@example.com"}
    body["records"].append(degree)
    complete = await client.patch("/api/profile", json=body, headers=auth_headers)
    assert complete.status_code == 200, complete.text
    assert complete.json()["notices"] == []


async def test_prose_fixture_keeps_publication_results_and_record_skills(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    profile = await ingest(client, auth_headers, prose_sources())
    by_title = {record["title"]: record for record in profile["records"]}

    role = by_title["Research Software Engineer"]
    assert role["skills"] == ["Python", "PyTorch", "FastAPI", "PostgreSQL", "Docker"]
    assert role["summary"].startswith("I joined Pellwater Hydrology Lab")
    assert "In my second year I moved from building the service" in role["summary"]

    paper = by_title["Gauge-Aware Sequence Models for Short-Horizon River Forecasting"]
    assert paper["category"] == "publication"
    assert [bullet["text"] for bullet in paper["bullets"]] == [
        "Status: Accepted, presented as a contributed talk",
        "Role: First author",
        "Lowered forecast error by 0.12 normalised RMSE on 37 held-out catchments",
        "Released a benchmark of 410,000 hourly gauge readings",
        "Matched the accuracy of the operational baseline with one third of the training data",
    ]
    assert all(bullet["source_ref"] is not None for bullet in paper["bullets"])
    assert UNCAPTURED not in codes(profile)


async def test_conflicting_contact_details_must_be_resolved_before_confirming(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    resume, linkedin, notes = sample_sources()
    linkedin["text"] = linkedin["text"].replace(
        "jordan.rivera@example.com", "someone.else@example.org"
    )
    profile = await ingest(client, auth_headers, [resume, linkedin, notes])

    assert profile["contact"]["email"] == "jordan.rivera@example.com"
    by_field = {conflict["field"]: conflict for conflict in profile["conflicts"]}
    assert set(by_field) == {"start_date", "contact_email"}
    contact_conflict = by_field["contact_email"]
    assert contact_conflict["resolution"] == "unresolved"
    assert contact_conflict["record_ids"] == []
    assert [
        (value["value"], value["source_ref"]["source_label"])
        for value in contact_conflict["values"]
    ] == [("jordan.rivera@example.com", "Resume"), ("someone.else@example.org", "LinkedIn")]

    blocked = await confirm(client, auth_headers, profile["version"])
    error = assert_error(blocked, 409, "unresolved_conflicts")
    assert error["details"] == {"unresolved_conflict_count": 2}

    resolved = await resolve_conflicts(client, auth_headers, profile)
    confirmed = await confirm(client, auth_headers, resolved["version"])
    assert confirmed.status_code == 200, confirmed.text


async def test_sample_profile_still_has_one_conflict_and_only_summary_notices(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    profile = await ingest(client, auth_headers)

    # Same name and e-mail in resume and LinkedIn: only the known date conflict.
    assert [conflict["field"] for conflict in profile["conflicts"]] == ["start_date"]
    # Name, contact details and a degree are there. What is reported is text
    # that is deliberately not a record: the general summary and "About"
    # paragraphs and the title line of the notes.
    assert codes(profile) == [UNCAPTURED] * 3
    messages = [notice["message"] for notice in profile["notices"]]
    assert [message.split(" was not captured")[0] for message in messages] == [
        "1 line of Resume",
        "1 line of LinkedIn",
        "1 line of Notes",
    ]

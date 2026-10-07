"""GET and PATCH /api/profile: reading, optimistic locking, provenance of
edits, and isolation between two sessions."""

from collections.abc import Awaitable, Callable

import httpx

from app.db import Database
from tests.conftest import SessionHandle
from tests.helpers import assert_error
from tests.integration.test_profile_flow import (
    confirmed_profile,
    evidence_documents,
    ingest,
    patch_body,
    sample_sources,
    simple_sources,
)

CreateSession = Callable[[], Awaitable[SessionHandle]]


async def test_profile_is_not_found_before_anything_was_ingested(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    assert_error(await client.get("/api/profile", headers=auth_headers), 404, "not_found")
    body = {"expected_version": 1, "contact": {}, "records": []}
    assert_error(
        await client.patch("/api/profile", json=body, headers=auth_headers), 404, "not_found"
    )
    assert_error(
        await client.post(
            "/api/profile/confirm", json={"expected_version": 1}, headers=auth_headers
        ),
        404,
        "not_found",
    )


async def test_get_returns_the_stored_profile(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    ingested = await ingest(client, auth_headers)
    response = await client.get("/api/profile", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == ingested


async def test_edit_changes_provenance_keeps_the_reference_and_bumps_the_version(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    profile = await ingest(client, auth_headers, simple_sources())
    role = profile["records"][0]
    body = patch_body(profile)
    body["records"][0]["bullets"][0]["text"] = "Built Python pipelines for 20 analysts"
    body["records"][0]["bullets"].append({"bullet_id": None, "text": "Wrote the runbook"})
    body["records"].append({"record_id": None, "category": "project", "title": "Bird Camera"})

    response = await client.patch("/api/profile", json=body, headers=auth_headers)

    assert response.status_code == 200
    updated = response.json()
    assert updated["version"] == profile["version"] + 1
    assert updated["profile_id"] == profile["profile_id"]
    assert updated["status"] == "draft"
    edited, unchanged, added = updated["records"][0]["bullets"]
    assert edited["provenance"] == "user_edited"
    # The original source reference is retained so the user can compare.
    assert edited["source_ref"] == role["bullets"][0]["source_ref"]
    assert edited["source_ref"]["excerpt"] == "Built Python pipelines for 12 analysts"
    assert unchanged == role["bullets"][1]
    assert (added["provenance"], added["source_ref"]) == ("user_added", None)
    assert updated["records"][0]["provenance"] == "extracted"
    assert updated["records"][-1]["provenance"] == "user_added"
    assert updated["records"][-1]["record_id"]
    assert (await client.get("/api/profile", headers=auth_headers)).json() == updated


async def test_stale_expected_version_is_a_conflict_and_changes_nothing(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    profile = await ingest(client, auth_headers, simple_sources())
    first = patch_body(profile)
    first["contact"]["phone"] = "555-0100"
    saved = (await client.patch("/api/profile", json=first, headers=auth_headers)).json()

    second = patch_body(profile)  # still says version 1
    second["contact"]["phone"] = "555-0199"
    error = assert_error(
        await client.patch("/api/profile", json=second, headers=auth_headers),
        409,
        "version_conflict",
    )

    assert error["details"] == {"current_version": 2}
    assert (await client.get("/api/profile", headers=auth_headers)).json() == saved


async def test_saving_without_changes_keeps_version_and_index(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    confirmed = await confirmed_profile(client, auth_headers, simple_sources())

    response = await client.patch("/api/profile", json=patch_body(confirmed), headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == confirmed
    assert response.json()["index_state"] == "indexed"


async def test_edit_after_confirmation_requires_confirming_again(
    client: httpx.AsyncClient, create_session: CreateSession, db: Database
) -> None:
    session = await create_session()
    confirmed = await confirmed_profile(client, session.headers, simple_sources())
    body = patch_body(confirmed)
    body["records"][0]["bullets"][0]["text"] = "Built Python pipelines for 20 analysts"

    edited = (await client.patch("/api/profile", json=body, headers=session.headers)).json()

    assert edited["version"] == confirmed["version"] + 1
    assert (edited["status"], edited["index_state"]) == ("draft", "not_indexed")
    assert edited["indexed_version"] is None
    assert edited["index_progress"] == {"total": 0, "embedded": 0}
    # Evidence of the confirmed version is kept for drafts that cite it; the
    # new version has none until it is confirmed.
    assert len(await evidence_documents(db, session.owner_id, confirmed["version"])) > 0
    assert await evidence_documents(db, session.owner_id, edited["version"]) == []


async def test_conflict_resolution_is_saved_with_its_note(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    profile = await ingest(client, auth_headers)
    conflict_id = profile["conflicts"][0]["conflict_id"]
    body = patch_body(
        profile,
        conflict_resolutions=[
            {"conflict_id": conflict_id, "resolution": "dismissed", "note": "  Both are fine.  "}
        ],
    )

    updated = (await client.patch("/api/profile", json=body, headers=auth_headers)).json()

    assert updated["conflicts"][0]["resolution"] == "dismissed"
    assert updated["conflicts"][0]["note"] == "Both are fine."
    assert updated["conflicts"][0]["values"] == profile["conflicts"][0]["values"]
    assert updated["review_summary"]["unresolved_conflict_count"] == 0


async def test_invalid_edits_are_rejected_with_the_field_at_fault(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    profile = await ingest(client, auth_headers, simple_sources())

    unknown_record = patch_body(profile)
    unknown_record["records"][0]["record_id"] = "f" * 32
    blank_title = patch_body(profile)
    blank_title["records"][0]["title"] = "   "
    bad_resolution = patch_body(
        profile, conflict_resolutions=[{"conflict_id": "nope", "resolution": "unresolved"}]
    )
    cases = [
        (unknown_record, "records.0.record_id"),
        (blank_title, "records.0.title"),
        (bad_resolution, "conflict_resolutions.0.resolution"),
        ({"contact": {}, "records": []}, "expected_version"),
    ]
    for body, field in cases:
        error = assert_error(
            await client.patch("/api/profile", json=body, headers=auth_headers),
            422,
            "validation_error",
        )
        assert field in [item["field"] for item in error["field_errors"]]
    assert (await client.get("/api/profile", headers=auth_headers)).json() == profile


async def test_two_sessions_cannot_see_or_change_each_others_profile(
    client: httpx.AsyncClient, create_session: CreateSession, db: Database
) -> None:
    alice, bob = await create_session(), await create_session()
    alice_profile = await confirmed_profile(client, alice.headers, sample_sources())

    # Bob has no profile: Alice's is invisible to him on every route.
    assert_error(await client.get("/api/profile", headers=bob.headers), 404, "not_found")
    assert_error(
        await client.patch("/api/profile", json=patch_body(alice_profile), headers=bob.headers),
        404,
        "not_found",
    )
    assert_error(
        await client.post(
            "/api/profile/confirm",
            json={"expected_version": alice_profile["version"]},
            headers=bob.headers,
        ),
        404,
        "not_found",
    )
    assert (await client.get("/api/session", headers=bob.headers)).json()["has_profile"] is False

    # Bob's own profile does not disturb Alice's, and IDs from Alice's profile
    # mean nothing inside his.
    bob_profile = await ingest(client, bob.headers, simple_sources())
    assert bob_profile["profile_id"] != alice_profile["profile_id"]
    assert "Jordan Rivera" not in str(bob_profile)
    foreign = patch_body(bob_profile)
    foreign["records"][0]["record_id"] = alice_profile["records"][0]["record_id"]
    assert_error(
        await client.patch("/api/profile", json=foreign, headers=bob.headers),
        422,
        "validation_error",
    )
    assert (await client.get("/api/profile", headers=alice.headers)).json() == alice_profile
    assert await db["sources"].count_documents({"owner_id": alice.owner_id}) == 3
    assert await db["sources"].count_documents({"owner_id": bob.owner_id}) == 1

"""The whole product through the HTTP API, with the fake provider and the
fictional fixtures in backend/fixtures.

Three stories:

1. One visitor goes from pasted sources to an edited, revalidated draft and
   then clears their data.
2. Two visitors do the same side by side and never see each other's data.
3. The server restarts in between and the stored work is still there.

The requests go through the real routes, middleware and MongoDB; only the AI
provider is the deterministic fake. The database is looked at directly only to
check what a client cannot see (that a collection really is empty).
"""

import json
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

import httpx
from fastapi import FastAPI

from app.config import Settings
from app.db import Database
from app.providers.fake.provider import FakeProvider
from app.schemas.common import utc_now
from tests.conftest import SessionHandle, client_for
from tests.helpers import assert_error
from tests.helpers_flow import (
    PROTECTED_REQUESTS,
    analysed_job,
    cited_evidence_ids,
    find_bullet,
    find_item,
    generated,
    injection_sources,
    keyed,
    post_generation,
    requirement_inputs,
)
from tests.helpers_generation import all_text, owned_counts
from tests.integration.test_profile_flow import (
    confirm,
    confirmed_profile,
    ingest,
    patch_body,
    sample_sources,
)

CreateSession = Callable[[], Awaitable[SessionHandle]]

EMPTY = {"sources": 0, "profiles": 0, "evidence": 0, "jobs": 0, "generations": 0}

RAG_BULLET_START = "Built a retrieval-augmented generation (RAG) service"
EDITED_RAG_BULLET = (
    "Built a retrieval-augmented generation (RAG) service in Python and FastAPI "
    "for customer support questions"
)
DOCKER_BULLET = (
    "Packaged model services as Docker images and cut image size from 1.4 GB to 380 MB "
    "with multi-stage builds"
)


async def open_evidence(
    client: httpx.AsyncClient, headers: dict[str, str], evidence_id: str
) -> dict[str, Any]:
    response = await client.get(f"/api/evidence/{evidence_id}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def profile_bullet(profile: dict[str, Any], fragment: str) -> dict[str, Any]:
    """The one bullet of a profile response whose text contains ``fragment``."""
    matches = [
        bullet
        for record in profile["records"]
        for bullet in record["bullets"]
        if fragment in bullet["text"]
    ]
    assert len(matches) == 1, f"{fragment!r} matches {len(matches)} profile bullets"
    return matches[0]


def with_bullet_text(profile: dict[str, Any], bullet_id: str, text: str) -> dict[str, Any]:
    """A PATCH /api/profile body that rewrites one bullet and keeps the rest."""
    body = patch_body(profile)
    for record in body["records"]:
        for bullet in record["bullets"]:
            if bullet["bullet_id"] == bullet_id:
                bullet["text"] = text
    return body


# ---- 1. One visitor, start to finish -----------------------------------------------


async def test_visitor_goes_from_sources_to_a_revalidated_draft_and_clears_their_data(
    client: httpx.AsyncClient, db: Database, fake_provider: FakeProvider
) -> None:
    # Create a session: the token is the only credential from here on.
    created = await client.post("/api/sessions")
    assert created.status_code == 201
    session = created.json()
    assert session["provider_mode"] == "fake"
    headers = {"Authorization": f"Bearer {session['token']}"}
    owner_id = (await db["sessions"].find_one({}))["owner_id"]

    # Ingest the three labelled sources: a draft that needs the user's attention.
    sources = sample_sources()
    draft = await ingest(client, headers, sources)
    assert (draft["version"], draft["status"], draft["index_state"]) == (1, "draft", "not_indexed")
    assert draft["review_summary"] == {"needs_review_count": 1, "unresolved_conflict_count": 1}

    # Get the profile: the stored draft is what ingestion returned.
    fetched = await client.get("/api/profile", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json() == draft

    # Patch an edit: the rewritten statement becomes the user's own.
    rag_bullet = profile_bullet(draft, RAG_BULLET_START)
    edit = with_bullet_text(draft, rag_bullet["bullet_id"], EDITED_RAG_BULLET)
    edited = (await client.patch("/api/profile", json=edit, headers=headers)).json()
    assert (edited["version"], edited["status"]) == (2, "draft")
    assert profile_bullet(edited, RAG_BULLET_START)["provenance"] == "user_edited"

    # Resolve the conflict: confirming is refused until that is done.
    blocked = await confirm(client, headers, edited["version"])
    assert_error(blocked, 409, "unresolved_conflicts")
    resolutions = [
        {
            "conflict_id": conflict["conflict_id"],
            "resolution": "resolved",
            "note": "Resume is right",
        }
        for conflict in edited["conflicts"]
    ]
    resolved = (
        await client.patch(
            "/api/profile",
            json=patch_body(edited, conflict_resolutions=resolutions),
            headers=headers,
        )
    ).json()
    assert resolved["version"] == 3
    assert resolved["review_summary"]["unresolved_conflict_count"] == 0

    # Confirm: evidence is built and embedded for exactly this version.
    confirmed = (await confirm(client, headers, 3)).json()
    assert (confirmed["status"], confirmed["index_state"]) == ("confirmed", "indexed")
    assert (confirmed["version"], confirmed["indexed_version"]) == (3, 3)
    chunks = confirmed["index_progress"]["total"]
    assert confirmed["index_progress"] == {"total": chunks, "embedded": chunks}
    assert chunks > 30

    # Create a job: every requirement points at its place in the posting.
    job = await analysed_job(client, headers, "close_fit")
    assert (job["version"], job["title"], job["company"]) == (
        1,
        "Applied Machine Learning Engineer",
        "Fernhollow AI",
    )
    for requirement in job["requirements"]:
        span = requirement["source_span"]
        assert job["description"][span["start"] : span["end"]] == span["excerpt"]

    # Patch the requirements: edit one, remove one, add one.
    requirements = requirement_inputs(job)
    removed = requirements.pop()
    requirements[0]["text"] = "Build retrieval-augmented generation (RAG) features"
    requirements.append(
        {
            "requirement_id": None,
            "text": "Experience with Redis",
            "category": "skill",
            "importance": "preferred",
        }
    )
    reviewed_job = await client.patch(
        f"/api/jobs/{job['job_id']}",
        json={"expected_version": 1, "requirements": requirements},
        headers=headers,
    )
    assert reviewed_job.status_code == 200, reviewed_job.text
    job = reviewed_job.json()
    assert job["version"] == 2
    assert job["requirements"][0]["user_edited"] is True
    added = job["requirements"][-1]
    assert (added["text"], added["user_edited"], added["source_span"]) == (
        "Experience with Redis",
        True,
        None,
    )

    # Generate: one draft for this profile version and job version.
    generation = await generated(client, headers, job["job_id"], "flow-generate-0001")
    generation_id = generation["generation_id"]
    assert generation["status"] == "completed"
    assert (generation["profile_version"], generation["job_version"]) == (3, 2)
    assert (generation["stale"], generation["stale_reasons"]) == (False, [])
    assert generation["provider_mode"] == "fake"
    assert generation["validation"]["state"] == "validated"
    assert generation["validation"]["unsupported_count"] == 0
    # The header of every role is the confirmed profile, word for word.
    assert [
        (entry["heading"], entry["subheading"], entry["date_range"])
        for entry in generation["resume"]["experience"]
    ] == [
        ("Machine Learning Engineer", "Brightloom Labs", "Aug 2024 - Present"),
        ("Software Engineer", "Quillfeather Software", "Jul 2022 - Jul 2024"),
        ("Software Engineering Intern", "Harborline Robotics", "May 2021 - Aug 2021"),
        ("Volunteer Web Developer", "Cedar Hollow Community Library", None),
    ]
    assert generation["resume"]["contact"] == confirmed["contact"]
    # One coverage item per reviewed requirement, in the job's order.
    assert [item["requirement_id"] for item in generation["coverage"]] == [
        requirement["requirement_id"] for requirement in job["requirements"]
    ]
    assert removed["requirement_id"] not in {
        item["requirement_id"] for item in generation["coverage"]
    }
    summary = generation["coverage_summary"]
    assert summary["assessed"] == summary["supported"] + summary["partial"] + summary["missing"]
    assert summary["assessed"] + summary["uncertain"] == len(job["requirements"])

    # The same Idempotency-Key again: the stored draft, no second provider call.
    calls_after_first = dict(fake_provider.calls)
    replay = await post_generation(client, headers, job["job_id"], "flow-generate-0001")
    assert replay.status_code == 200
    assert replay.json() == generation
    assert dict(fake_provider.calls) == calls_after_first

    # Get the generation: the same document.
    read_back = await client.get(f"/api/generations/{generation_id}", headers=headers)
    assert read_back.status_code == 200
    assert read_back.json() == generation

    # Fetch the evidence behind every citation.
    source_text = {item["label"]: item["text"] for item in sources}
    cited = cited_evidence_ids(generation)
    assert len(cited) >= len(generation["retrieved_evidence_ids"]) > 0
    provenances = set()
    for evidence_id in cited:
        evidence = await open_evidence(client, headers, evidence_id)
        assert "embedding" not in evidence
        assert evidence["profile_version"] == 3
        provenances.add(evidence["provenance"])
        cited_source = evidence["source"]
        if evidence["provenance"] == "extracted":
            # The excerpt is the stored source text, character for character.
            original = source_text[cited_source["label"]]
            assert original[cited_source["start"] : cited_source["end"]] == evidence["excerpt"]
        else:
            # The user's own wording: no source span is claimed for it.
            assert evidence["provenance"] == "user_edited"
            assert evidence["excerpt"] == EDITED_RAG_BULLET
            assert cited_source == {
                "source_id": None,
                "label": "Edited during profile review",
                "source_type": "user",
                "start": None,
                "end": None,
            }
    assert provenances == {"extracted", "user_edited"}

    # Patch a manual edit: the statement loses its verdict until revalidated.
    docker_item = find_bullet(generation, "experience", "Packaged model services")
    assert (docker_item["text"], docker_item["validation_status"]) == (DOCKER_BULLET, "supported")
    kubernetes_text = "Packaged model services as Docker images and ran them on Kubernetes"
    patched = await client.patch(
        f"/api/generations/{generation_id}",
        json={
            "expected_revision": generation["revision"],
            "edits": [{"item_id": docker_item["item_id"], "text": kubernetes_text}],
        },
        headers=headers,
    )
    assert patched.status_code == 200, patched.text
    patched = patched.json()
    edited_item = find_item(patched, docker_item["item_id"])
    assert (edited_item["validation_status"], edited_item["user_edited"]) == ("user_edited", True)
    assert patched["validation"]["state"] == "needs_revalidation"
    assert patched["validation"]["user_edited_count"] == 1
    assert patched["revision"] == generation["revision"] + 1

    # Validate: the edit claims something the profile does not contain.
    validated = await client.post(f"/api/generations/{generation_id}/validate", headers=headers)
    assert validated.status_code == 200, validated.text
    validated = validated.json()
    checked_item = find_item(validated, docker_item["item_id"])
    assert checked_item["text"] == kubernetes_text  # validation never rewrites
    assert checked_item["validation_status"] == "unsupported"
    assert any("Kubernetes" in warning for warning in checked_item["warnings"])
    assert validated["validation"]["state"] == "validated"
    assert validated["validation"]["unsupported_count"] == 1

    # Regenerate that one item: it is rewritten from its evidence and checked.
    regenerate_url = f"/api/generations/{generation_id}/items/{docker_item['item_id']}/regenerate"
    regenerated = await client.post(
        regenerate_url, json={"instruction": None}, headers=keyed(headers, "flow-regen-0001")
    )
    assert regenerated.status_code == 200, regenerated.text
    regenerated = regenerated.json()
    rewritten = find_item(regenerated, docker_item["item_id"])
    assert rewritten["text"] == DOCKER_BULLET
    assert (rewritten["validation_status"], rewritten["user_edited"]) == ("supported", False)
    assert regenerated["validation"]["unsupported_count"] == 0
    assert "Kubernetes" not in all_text(regenerated)
    calls_after_regeneration = fake_provider.calls["regenerate_item"]
    again = await client.post(regenerate_url, headers=keyed(headers, "flow-regen-0001"))
    assert again.status_code == 200
    assert fake_provider.calls["regenerate_item"] == calls_after_regeneration == 1

    # Edit the profile: the draft is stale and nothing new can be generated
    # until the edited profile is confirmed again.
    current = (await client.get("/api/profile", headers=headers)).json()
    volunteer_bullet = profile_bullet(current, "Rebuilt the library events page")
    after_edit = await client.patch(
        "/api/profile",
        json=with_bullet_text(
            current, volunteer_bullet["bullet_id"], "Rebuilt the library events page"
        ),
        headers=headers,
    )
    assert after_edit.status_code == 200, after_edit.text
    assert (after_edit.json()["version"], after_edit.json()["status"]) == (4, "draft")
    stale = (await client.get(f"/api/generations/{generation_id}", headers=headers)).json()
    assert (stale["stale"], stale["stale_reasons"]) == (True, ["profile_changed"])
    listing = (await client.get("/api/generations", headers=headers)).json()["generations"]
    assert [(item["generation_id"], item["stale"]) for item in listing] == [(generation_id, True)]
    refused = await post_generation(client, headers, job["job_id"], "flow-generate-0002")
    assert_error(refused, 409, "profile_not_confirmed")
    # A stale draft still shows where its statements came from.
    await open_evidence(client, headers, rewritten["evidence_ids"][0])

    # Delete the session: everything is gone and the token is dead.
    deleted = await client.delete("/api/session", headers=headers)
    assert deleted.status_code == 200
    assert deleted.json() == {
        "deleted": True,
        "deleted_counts": {
            "sources": 3,
            "profiles": 1,
            "evidence": chunks,
            "jobs": 1,
            "generations": 1,
        },
    }
    real_requests = [
        ("GET", "/api/profile", None),
        ("GET", f"/api/jobs/{job['job_id']}", None),
        ("GET", f"/api/generations/{generation_id}", None),
        ("POST", f"/api/generations/{generation_id}/validate", None),
        ("GET", f"/api/evidence/{cited[0]}", None),
    ]
    for method, url, body in [*PROTECTED_REQUESTS, *real_requests]:
        response = await client.request(
            method, url, json=body, headers=keyed(headers, "flow-after-delete")
        )
        assert_error(response, 401, "unauthorized")
    assert await owned_counts(db, owner_id) == EMPTY


# ---- 2. Two visitors side by side --------------------------------------------------


async def test_two_sessions_stay_isolated_from_sources_to_deletion(
    client: httpx.AsyncClient, create_session: CreateSession, db: Database
) -> None:
    jordan = await create_session()
    casey = await create_session()

    # Both run the whole pipeline with their own sources and their own job.
    jordan_profile = await confirmed_profile(client, jordan.headers, sample_sources())
    casey_profile = await confirmed_profile(client, casey.headers, injection_sources())
    jordan_job = await analysed_job(client, jordan.headers, "close_fit")
    casey_job = await analysed_job(client, casey.headers, "injection_job")
    # The same Idempotency-Key in both sessions must not collide.
    jordan_draft = await generated(client, jordan.headers, jordan_job["job_id"], "shared-key-0001")
    casey_draft = await generated(client, casey.headers, casey_job["job_id"], "shared-key-0001")
    assert jordan_draft["generation_id"] != casey_draft["generation_id"]

    # Sources and profile: each visitor reads only their own.
    assert jordan_profile["contact"]["name"] == "Jordan Rivera"
    assert casey_profile["contact"]["name"] == "Casey Tran"
    assert (await client.get("/api/profile", headers=casey.headers)).json() == casey_profile
    assert [item["char_count"] for item in casey_profile["sources"]] == [
        len(injection_sources()[0]["text"])
    ]
    assert not {item["source_id"] for item in casey_profile["sources"]} & {
        item["source_id"] for item in jordan_profile["sources"]
    }

    # Retrieval: a draft is built from, and cites, only its owner's evidence.
    for owner, draft, own_words, foreign_words in (
        (jordan, jordan_draft, "Brightloom", ("Saltmarsh", "Owlcrest", "Casey", "Django")),
        (casey, casey_draft, "Saltmarsh", ("Brightloom", "Quillfeather", "Jordan", "TrailNotes")),
    ):
        evidence_ids = cited_evidence_ids(draft)
        assert evidence_ids
        owners = await db["evidence"].distinct("owner_id", {"_id": {"$in": evidence_ids}})
        assert owners == [owner.owner_id]
        document = json.dumps(draft)
        assert own_words in document
        assert not [word for word in foreign_words if word in document]

    # Evidence, jobs and drafts of the other visitor look exactly like missing ones.
    jordan_evidence_id = cited_evidence_ids(jordan_draft)[0]
    jordan_item_id = jordan_draft["resume"]["experience"][0]["bullets"][0]["item_id"]
    jordan_generation_url = f"/api/generations/{jordan_draft['generation_id']}"
    foreign_requests: list[tuple[str, str, dict[str, Any] | None]] = [
        ("GET", f"/api/evidence/{jordan_evidence_id}", None),
        ("GET", f"/api/jobs/{jordan_job['job_id']}", None),
        (
            "PATCH",
            f"/api/jobs/{jordan_job['job_id']}",
            {"expected_version": 1, "requirements": []},
        ),
        ("POST", "/api/generations", {"job_id": jordan_job["job_id"]}),
        ("GET", jordan_generation_url, None),
        (
            "PATCH",
            jordan_generation_url,
            {
                "expected_revision": jordan_draft["revision"],
                "edits": [{"item_id": jordan_item_id, "text": "Taken over"}],
            },
        ),
        ("POST", f"{jordan_generation_url}/validate", None),
        ("POST", f"{jordan_generation_url}/items/{jordan_item_id}/regenerate", None),
    ]
    for method, url, body in foreign_requests:
        response = await client.request(
            method, url, json=body, headers=keyed(casey.headers, "casey-foreign-0001")
        )
        assert_error(response, 404, "not_found")
    # An item of Jordan's draft cannot be reached through Casey's own draft either.
    crossed = await client.post(
        f"/api/generations/{casey_draft['generation_id']}/items/{jordan_item_id}/regenerate",
        headers=keyed(casey.headers, "casey-foreign-0002"),
    )
    assert_error(crossed, 404, "not_found")
    unchanged = await client.get(jordan_generation_url, headers=jordan.headers)
    assert unchanged.json() == jordan_draft

    # Lists show only the caller's own records.
    casey_jobs = (await client.get("/api/jobs", headers=casey.headers)).json()["jobs"]
    assert [item["job_id"] for item in casey_jobs] == [casey_job["job_id"]]
    casey_drafts = (await client.get("/api/generations", headers=casey.headers)).json()
    assert [item["generation_id"] for item in casey_drafts["generations"]] == [
        casey_draft["generation_id"]
    ]

    # Deletion: Jordan's data is removed, Casey's is untouched and still usable.
    casey_counts = await owned_counts(db, casey.owner_id)
    deleted = await client.delete("/api/session", headers=jordan.headers)
    assert deleted.status_code == 200
    assert await owned_counts(db, jordan.owner_id) == EMPTY
    assert await owned_counts(db, casey.owner_id) == casey_counts
    assert_error(await client.get("/api/profile", headers=jordan.headers), 401, "unauthorized")
    assert (await client.get("/api/profile", headers=casey.headers)).json() == casey_profile
    still_there = await client.get(
        f"/api/generations/{casey_draft['generation_id']}", headers=casey.headers
    )
    assert still_there.json() == casey_draft
    await open_evidence(client, casey.headers, cited_evidence_ids(casey_draft)[0])
    another = await generated(client, casey.headers, casey_job["job_id"], "casey-second-0001")
    assert another["generation_id"] != casey_draft["generation_id"]


# ---- 3. A restart in between -------------------------------------------------------


async def test_restarted_server_serves_stored_work_and_replays_without_a_provider_call(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    db: Database,
    settings: Settings,
    make_app: Callable[[Settings], Awaitable[FastAPI]],
) -> None:
    session = await create_session()
    headers = session.headers
    profile = await confirmed_profile(client, headers, sample_sources())
    job = await analysed_job(client, headers, "partial_fit")
    draft = await generated(client, headers, job["job_id"], "restart-generate-0001")
    evidence_id = cited_evidence_ids(draft)[0]
    evidence = await open_evidence(client, headers, evidence_id)

    # A second application instance with its own database client and its own
    # (unused) provider stands in for the restarted server.
    restarted = await make_app(settings)
    new_provider = restarted.state.provider
    assert isinstance(new_provider, FakeProvider)
    async with client_for(restarted) as new_client:
        session_info = await new_client.get("/api/session", headers=headers)
        assert session_info.status_code == 200
        assert session_info.json()["has_profile"] is True
        assert (await new_client.get("/api/profile", headers=headers)).json() == profile
        assert (await new_client.get(f"/api/jobs/{job['job_id']}", headers=headers)).json() == job
        read_back = await new_client.get(
            f"/api/generations/{draft['generation_id']}", headers=headers
        )
        assert read_back.json() == draft
        assert await open_evidence(new_client, headers, evidence_id) == evidence

        # Idempotency and quota are database state, not process memory: the
        # repeated key is answered from storage and charged nothing.
        replay = await post_generation(new_client, headers, job["job_id"], "restart-generate-0001")
        assert replay.status_code == 200
        assert replay.json() == draft
        assert sum(new_provider.calls.values()) == 0
        stored_session = await db["sessions"].find_one({"_id": session.session_id})
        assert stored_session["quota"]["generation"] == 1

        # Survival applies to records that have not expired. Once the session's
        # time is up the restarted server refuses it, before any TTL cleanup.
        await db["sessions"].update_one(
            {"_id": session.session_id},
            {"$set": {"expires_at": utc_now() - timedelta(seconds=1)}},
        )
        expired = await new_client.get("/api/profile", headers=headers)
        assert_error(expired, 401, "session_expired")
        expired_draft = await new_client.get(
            f"/api/generations/{draft['generation_id']}", headers=headers
        )
        assert_error(expired_draft, 401, "session_expired")

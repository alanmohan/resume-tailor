"""The profile pipeline end to end through the HTTP API: ingest the fictional
sample, review it, confirm it, open a citation, restart the server.

The helper functions at the top are shared by the other profile, indexing,
evidence and job API tests."""

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI

from app.config import Settings
from app.db import Database
from tests.conftest import SessionHandle, client_for
from tests.helpers import parse_iso_z

CreateSession = Callable[[], Awaitable[SessionHandle]]

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"

EDITABLE_RECORD_FIELDS = (
    "record_id",
    "category",
    "title",
    "organization",
    "location",
    "start_date",
    "end_date",
    "summary",
    "skills",
)


def fixture_text(relative_path: str) -> str:
    return (FIXTURES / relative_path).read_text(encoding="utf-8")


def source(label: str, source_type: str, text: str) -> dict[str, str]:
    return {"label": label, "source_type": source_type, "text": text}


def sample_sources() -> list[dict[str, str]]:
    """The fictional candidate's resume, LinkedIn text and notes."""
    return [
        source("Resume", "resume", fixture_text("profiles/sample_resume.txt")),
        source("LinkedIn", "linkedin", fixture_text("profiles/sample_linkedin.txt")),
        source("Notes", "notes", fixture_text("profiles/sample_notes.txt")),
    ]


def simple_sources() -> list[dict[str, str]]:
    """A small profile without conflicts, for tests about something else."""
    text = (
        "Riley Park\nriley.park@example.com\n\n"
        "Experience\n"
        "Data Engineer - Northwind Labs (Jan 2020 - Mar 2021)\n"
        "- Built Python pipelines for 12 analysts\n"
        "- Cut report time from 3 hours to 20 minutes\n\n"
        "Skills\nLanguages: Python, SQL\n"
    )
    return [source("Resume", "resume", text)]


def patch_body(profile: dict[str, Any], **extra: Any) -> dict[str, Any]:
    """The PATCH /api/profile body that saves ``profile`` exactly as it is."""
    records = [
        {name: record[name] for name in EDITABLE_RECORD_FIELDS}
        | {
            "bullets": [
                {"bullet_id": bullet["bullet_id"], "text": bullet["text"]}
                for bullet in record["bullets"]
            ]
        }
        for record in profile["records"]
    ]
    body = {
        "expected_version": profile["version"],
        "contact": profile["contact"],
        "records": records,
    }
    return body | extra


async def ingest(
    client: httpx.AsyncClient, headers: dict[str, str], sources: list[dict[str, str]] | None = None
) -> dict[str, Any]:
    response = await client.post(
        "/api/profiles/ingest", json={"sources": sources or sample_sources()}, headers=headers
    )
    assert response.status_code == 200, response.text
    return response.json()


async def resolve_conflicts(
    client: httpx.AsyncClient, headers: dict[str, str], profile: dict[str, Any]
) -> dict[str, Any]:
    resolutions = [
        {"conflict_id": conflict["conflict_id"], "resolution": "resolved"}
        for conflict in profile["conflicts"]
    ]
    response = await client.patch(
        "/api/profile", json=patch_body(profile, conflict_resolutions=resolutions), headers=headers
    )
    assert response.status_code == 200, response.text
    return response.json()


async def confirm(
    client: httpx.AsyncClient, headers: dict[str, str], version: int
) -> httpx.Response:
    return await client.post(
        "/api/profile/confirm", json={"expected_version": version}, headers=headers
    )


async def confirmed_profile(
    client: httpx.AsyncClient, headers: dict[str, str], sources: list[dict[str, str]] | None = None
) -> dict[str, Any]:
    """Ingest, resolve every conflict and confirm; returns the indexed profile."""
    profile = await ingest(client, headers, sources)
    if profile["conflicts"]:
        profile = await resolve_conflicts(client, headers, profile)
    response = await confirm(client, headers, profile["version"])
    assert response.status_code == 200, response.text
    return response.json()


async def evidence_documents(db: Database, owner_id: str, version: int) -> list[dict[str, Any]]:
    """The stored evidence of one profile version, in position order."""
    cursor = db["evidence"].find({"owner_id": owner_id, "profile_version": version})
    return [document async for document in cursor.sort("position", 1)]


# ---- The flow ----------------------------------------------------------------------


async def test_ingest_review_confirm_and_open_a_citation(
    client: httpx.AsyncClient, create_session: CreateSession, db: Database
) -> None:
    session = await create_session()
    headers = session.headers

    # 1. Ingest: a draft that still needs the user's attention.
    draft = await ingest(client, headers)
    assert (draft["version"], draft["status"], draft["index_state"]) == (1, "draft", "not_indexed")
    assert draft["review_summary"] == {"needs_review_count": 1, "unresolved_conflict_count": 1}
    assert (await client.get("/api/session", headers=headers)).json()["has_profile"] is True

    # 2. It cannot be confirmed while the date conflict is open.
    blocked = await confirm(client, headers, draft["version"])
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "unresolved_conflicts"

    # 3. Review: resolve the conflict and correct one statement.
    body = patch_body(
        draft,
        conflict_resolutions=[
            {
                "conflict_id": draft["conflicts"][0]["conflict_id"],
                "resolution": "resolved",
                "note": "The resume has the right month.",
            }
        ],
    )
    body["records"][0]["bullets"][0]["text"] = "Built a RAG service in Python and FastAPI"
    reviewed = (await client.patch("/api/profile", json=body, headers=headers)).json()
    assert (reviewed["version"], reviewed["status"]) == (2, "draft")
    assert reviewed["review_summary"]["unresolved_conflict_count"] == 0
    edited = reviewed["records"][0]["bullets"][0]
    assert edited["provenance"] == "user_edited"

    # 4. Confirm: evidence is built and embedded for exactly this version.
    confirmed = (await confirm(client, headers, 2)).json()
    assert (confirmed["version"], confirmed["status"]) == (2, "confirmed")
    assert (confirmed["index_state"], confirmed["indexed_version"]) == ("indexed", 2)
    total = confirmed["index_progress"]["total"]
    assert confirmed["index_progress"] == {"total": total, "embedded": total}
    assert confirmed["index_error"] is None
    stored = await evidence_documents(db, session.owner_id, 2)
    assert len(stored) == total > 30

    # 5. A citation opens the exact excerpt, its source and its role.
    cited = next(doc for doc in stored if "20%" in doc["text"])
    evidence = (await client.get(f"/api/evidence/{cited['_id']}", headers=headers)).json()
    assert evidence["excerpt"] == (
        "Improved unit test coverage by 20% by adding pytest suites for the billing "
        "and export modules"
    )
    assert evidence["source"]["label"] == "Resume"
    assert evidence["parent"]["organization"] == "Quillfeather Software"
    resume = sample_sources()[0]["text"]
    assert resume[evidence["source"]["start"] : evidence["source"]["end"]] == evidence["excerpt"]

    # The edited statement is the user's own evidence now.
    own = next(doc for doc in stored if doc["bullet_id"] == edited["bullet_id"])
    own_evidence = (await client.get(f"/api/evidence/{own['_id']}", headers=headers)).json()
    assert own_evidence["provenance"] == "user_edited"
    assert own_evidence["excerpt"] == "Built a RAG service in Python and FastAPI"
    assert own_evidence["source"]["source_id"] is None


async def test_profile_response_has_the_contract_shape(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    profile = await ingest(client, auth_headers)

    assert set(profile) == {
        "profile_id",
        "version",
        "status",
        "index_state",
        "indexed_version",
        "index_progress",
        "index_error",
        "contact",
        "records",
        "conflicts",
        "sources",
        "review_summary",
        "created_at",
        "updated_at",
        "expires_at",
    }
    assert set(profile["records"][0]) == {
        "record_id",
        "category",
        "title",
        "organization",
        "location",
        "start_date",
        "end_date",
        "summary",
        "bullets",
        "skills",
        "source_ref",
        "provenance",
        "needs_review",
        "review_reasons",
    }
    assert set(profile["records"][0]["bullets"][0]) == {
        "bullet_id",
        "text",
        "source_ref",
        "provenance",
        "needs_review",
        "review_reasons",
    }
    assert set(profile["records"][0]["source_ref"]) == {
        "source_id",
        "source_label",
        "start",
        "end",
        "excerpt",
    }
    assert [(s["label"], s["source_type"], s["revision"]) for s in profile["sources"]] == [
        ("Resume", "resume", 1),
        ("LinkedIn", "linkedin", 1),
        ("Notes", "notes", 1),
    ]
    assert profile["sources"][0]["char_count"] == len(sample_sources()[0]["text"])
    for stamp in ("created_at", "updated_at", "expires_at"):
        parse_iso_z(profile[stamp])
    # Internal fields never leave the server.
    assert "owner_id" not in profile and "text" not in profile["sources"][0]


async def test_stored_records_survive_a_server_restart(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    settings: Settings,
    make_app: Callable[[Settings], Awaitable[FastAPI]],
) -> None:
    confirmed = await confirmed_profile(client, auth_headers, simple_sources())

    # A second application instance with its own database client stands in for
    # the restarted server; nothing was kept in process memory.
    restarted = await make_app(settings)
    async with client_for(restarted) as new_client:
        response = await new_client.get("/api/profile", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == confirmed

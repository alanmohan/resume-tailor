"""EM-01 through the API: skills listed under a role are stored as evidence,
count against MAX_EVIDENCE_CHUNKS and use the embedding cache like any other
record."""

from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from app.config import Settings
from app.db import Database
from app.providers.fake.provider import FakeProvider
from tests.conftest import SessionHandle, client_for
from tests.helpers import assert_error
from tests.integration.test_index_confirm_api import record_embedded_texts
from tests.integration.test_profile_flow import (
    confirm,
    evidence_documents,
    ingest,
    patch_body,
    simple_sources,
)

CreateSession = Callable[[], Awaitable[SessionHandle]]
MakeApp = Callable[[Settings], Awaitable[FastAPI]]
MakeSettings = Callable[..., Settings]

ROLE_SKILLS_TEXT = "[Data Engineer at Northwind Labs] Skills: Airflow, dbt"


async def with_role_skills(
    client: httpx.AsyncClient, headers: dict[str, str], profile: dict[str, Any]
) -> dict[str, Any]:
    """Add two skills to the role during review (they are in no bullet)."""
    body = patch_body(profile)
    body["records"][0]["skills"] = ["Airflow", "dbt"]
    response = await client.patch("/api/profile", json=body, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def test_role_skills_are_stored_embedded_and_can_be_opened(
    client: httpx.AsyncClient, create_session: CreateSession, db: Database
) -> None:
    session = await create_session()
    profile = await with_role_skills(
        client, session.headers, await ingest(client, session.headers, simple_sources())
    )

    confirmed = (await confirm(client, session.headers, profile["version"])).json()

    stored = await evidence_documents(db, session.owner_id, profile["version"])
    assert confirmed["index_progress"] == {"total": 5, "embedded": 5}
    skills = next(document for document in stored if document["text"] == ROLE_SKILLS_TEXT)
    assert skills["embedding_status"] == "embedded"
    opened = (await client.get(f"/api/evidence/{skills['_id']}", headers=session.headers)).json()
    assert opened["category"] == "skill"
    assert opened["excerpt"] == "Skills: Airflow, dbt"
    assert opened["tags"] == ["airflow", "dbt"]
    assert opened["parent"]["record_id"] == profile["records"][0]["record_id"]
    assert opened["parent"]["category"] == "employment"


async def test_role_skills_use_the_embedding_cache_after_an_unrelated_edit(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    fake_provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = await create_session()
    profile = await with_role_skills(
        client, session.headers, await ingest(client, session.headers, simple_sources())
    )
    confirmed = (await confirm(client, session.headers, profile["version"])).json()
    body = patch_body(confirmed)
    body["records"][0]["bullets"][1]["text"] = "Cut report time from 3 hours to 15 minutes"
    edited = (await client.patch("/api/profile", json=body, headers=session.headers)).json()
    embedded = record_embedded_texts(fake_provider, monkeypatch)

    assert (await confirm(client, session.headers, edited["version"])).status_code == 200

    assert embedded == [
        ["[Data Engineer at Northwind Labs] Cut report time from 3 hours to 15 minutes"]
    ]


async def test_role_skills_count_against_the_evidence_chunk_limit(
    make_app: MakeApp, make_settings: MakeSettings
) -> None:
    application = await make_app(make_settings(max_evidence_chunks=4))
    async with client_for(application) as client:
        token = (await client.post("/api/sessions")).json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        profile = await ingest(client, headers, simple_sources())
        # Four records fit exactly; the skills line of the role is a fifth.
        profile = await with_role_skills(client, headers, profile)

        error = assert_error(
            await confirm(client, headers, profile["version"]), 422, "too_many_chunks"
        )

    assert error["details"] == {"chunks": 5, "limit": 4}
    assert application.state.provider.calls["embed"] == 0

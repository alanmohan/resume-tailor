"""SEC-4: half of an emoji (an unpaired surrogate) in pasted text.

JSON can carry one as an escape such as "\\ud83d". Such text cannot be encoded
as UTF-8, so it used to fail deep inside the provider call with a 500, after
the quota had been charged, and every retry charged again.
"""

import json
from collections.abc import Awaitable, Callable

import httpx
import pytest
from pydantic import ValidationError

from app.db import Database
from app.providers.fake.provider import FakeProvider
from app.schemas.jobs import JobCreateRequest
from app.schemas.profiles import SourceInput
from tests.conftest import SessionHandle
from tests.helpers import assert_error

CreateSession = Callable[[], Awaitable[SessionHandle]]

# Raw JSON, exactly as a browser sends it: the escape stays an escape.
BROKEN_ESCAPE = "\\ud83d"
BROKEN_CHARACTER = "\ud83d"
MESSAGE_PART = "broken character"


def raw_json(template: dict) -> bytes:
    """Serialise ``template`` and put the escape in place of the marker."""
    return json.dumps(template).replace("<BROKEN>", BROKEN_ESCAPE).encode()


async def post_raw(
    client: httpx.AsyncClient, path: str, body: dict, headers: dict[str, str]
) -> httpx.Response:
    return await client.post(
        path, content=raw_json(body), headers={**headers, "Content-Type": "application/json"}
    )


async def quota_used(db: Database, session: SessionHandle) -> dict[str, int]:
    return (await db["sessions"].find_one({"_id": session.session_id}))["quota"]


def test_request_models_reject_text_that_is_not_valid_unicode() -> None:
    with pytest.raises(ValidationError) as source_error:
        SourceInput(label="Resume", source_type="resume", text=f"Built {BROKEN_CHARACTER} tools")
    with pytest.raises(ValidationError) as job_error:
        JobCreateRequest(description=f"Needs Python {BROKEN_CHARACTER} skills")

    assert MESSAGE_PART in str(source_error.value)
    assert MESSAGE_PART in str(job_error.value)
    # A complete emoji and other non-ASCII text are fine.
    assert SourceInput(label="Resume", source_type="resume", text="Señor engineer 🚀").text
    assert JobCreateRequest(description="Bäckerei sucht Entwickler 🚀").description


async def test_broken_character_in_resume_text_is_a_field_error_before_any_charge(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    db: Database,
    fake_provider: FakeProvider,
) -> None:
    session = await create_session()
    body = {
        "sources": [
            {"label": "Resume", "source_type": "resume", "text": "Data Engineer <BROKEN> at Acme"}
        ]
    }

    for _ in range(2):  # a retry must not be charged either
        response = await post_raw(client, "/api/profiles/ingest", body, session.headers)
        error = assert_error(response, 422, "validation_error")
        assert error["field_errors"][0]["field"] == "sources.0.text"
        assert MESSAGE_PART in error["field_errors"][0]["message"]

    assert (await quota_used(db, session))["ingest"] == 0
    assert sum(fake_provider.calls.values()) == 0
    assert await db["rate_limits"].count_documents({"_id": {"$regex": "^ai_calls:"}}) == 0
    assert await db["sources"].count_documents({"owner_id": session.owner_id}) == 0


async def test_broken_character_in_a_job_description_is_a_field_error_before_any_charge(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    db: Database,
    fake_provider: FakeProvider,
) -> None:
    session = await create_session()
    body = {"description": "Needs Python <BROKEN> skills and five years of experience."}

    response = await post_raw(client, "/api/jobs", body, session.headers)

    error = assert_error(response, 422, "validation_error")
    assert error["field_errors"][0]["field"] == "description"
    assert MESSAGE_PART in error["field_errors"][0]["message"]
    assert (await quota_used(db, session))["job_analysis"] == 0
    assert sum(fake_provider.calls.values()) == 0
    assert await db["jobs"].count_documents({"owner_id": session.owner_id}) == 0

"""Session creation, reading, deletion and every way a token can be rejected."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.db import Database
from app.repositories import Repositories
from app.schemas.common import utc_now
from app.security import generate_token, hash_token, is_well_formed_token
from tests.conftest import SessionHandle
from tests.factories import make_profile
from tests.helpers import assert_error, parse_iso_z

CreateSession = Callable[[], Awaitable[SessionHandle]]


async def test_create_session_returns_token_expiry_mode_and_limits(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post("/api/sessions")

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"token", "expires_at", "provider_mode", "limits"}
    assert is_well_formed_token(body["token"])
    assert body["provider_mode"] == "fake"
    assert body["limits"] == {
        "max_profile_chars": 60_000,
        "max_job_chars": 25_000,
        "max_sources": 5,
        "max_requirements": 25,
        "session_ttl_hours": 24,
    }
    lifetime = parse_iso_z(body["expires_at"]) - datetime.now(UTC)
    assert timedelta(hours=23, minutes=59) < lifetime <= timedelta(hours=24)


async def test_only_the_token_hash_is_stored(client: httpx.AsyncClient, db: Database) -> None:
    token = (await client.post("/api/sessions")).json()["token"]

    documents = [document async for document in db["sessions"].find({})]
    assert len(documents) == 1
    stored = documents[0]
    assert stored["token_hash"] == hash_token(token)
    assert token not in repr(stored)
    assert stored["revoked_at"] is None
    assert stored["owner_id"] != stored["_id"]
    assert stored["quota"] == {
        "ingest": 0,
        "confirm": 0,
        "job_analysis": 0,
        "generation": 0,
        "regeneration": 0,
        "validation": 0,
    }
    # BSON date, timezone-aware when read back.
    assert stored["expires_at"].tzinfo is not None


async def test_each_session_gets_its_own_token_and_owner(create_session: CreateSession) -> None:
    first = await create_session()
    second = await create_session()
    assert first.token != second.token
    assert first.owner_id != second.owner_id


async def test_read_session_reports_expiry_limits_and_profile_presence(
    client: httpx.AsyncClient, create_session: CreateSession, repos: Repositories
) -> None:
    session = await create_session()

    before = await client.get("/api/session", headers=session.headers)
    assert before.status_code == 200
    body = before.json()
    assert set(body) == {"expires_at", "provider_mode", "limits", "has_profile"}
    assert body["has_profile"] is False
    assert parse_iso_z(body["expires_at"]) == session.expires_at
    assert "token" not in body and "owner_id" not in body

    await repos.profiles.create(session.owner_id, make_profile(session.owner_id))
    after = await client.get("/api/session", headers=session.headers)
    assert after.json()["has_profile"] is True


async def test_has_profile_is_scoped_to_the_caller(
    client: httpx.AsyncClient, create_session: CreateSession, repos: Repositories
) -> None:
    alice = await create_session()
    bob = await create_session()
    await repos.profiles.create(alice.owner_id, make_profile(alice.owner_id))

    assert (await client.get("/api/session", headers=bob.headers)).json()["has_profile"] is False


async def test_missing_token_is_unauthorized(client: httpx.AsyncClient) -> None:
    assert_error(await client.get("/api/session"), 401, "unauthorized")
    assert_error(await client.delete("/api/session"), 401, "unauthorized")


@pytest.mark.parametrize(
    "header",
    [
        "Bearer",
        "Bearer short",
        "Bearer " + "a" * 200,
        "Bearer has spaces in the middle of this token value",
        "Bearer bad$characters$" + "a" * 40,
        "Basic dXNlcjpwYXNzd29yZA==",
        "Token " + "a" * 43,
        "a" * 43,
    ],
)
async def test_malformed_authorization_header_is_unauthorized(
    client: httpx.AsyncClient, header: str
) -> None:
    response = await client.get("/api/session", headers={"Authorization": header})
    assert_error(response, 401, "unauthorized")


async def test_unknown_token_is_unauthorized(
    client: httpx.AsyncClient, create_session: CreateSession
) -> None:
    await create_session()
    headers = {"Authorization": f"Bearer {generate_token()}"}
    assert_error(await client.get("/api/session", headers=headers), 401, "unauthorized")


async def test_the_token_hash_itself_is_not_accepted_as_a_token(
    client: httpx.AsyncClient, create_session: CreateSession
) -> None:
    session = await create_session()
    headers = {"Authorization": f"Bearer {hash_token(session.token)}"}
    assert_error(await client.get("/api/session", headers=headers), 401, "unauthorized")


async def test_delete_session_revokes_the_token(
    client: httpx.AsyncClient, create_session: CreateSession, db: Database
) -> None:
    session = await create_session()

    response = await client.delete("/api/session", headers=session.headers)
    assert response.status_code == 200
    assert response.json() == {
        "deleted": True,
        "deleted_counts": {"sources": 0, "profiles": 0, "evidence": 0, "jobs": 0, "generations": 0},
    }

    # The session stays as a revoked tombstone, and the token no longer works.
    stored = await db["sessions"].find_one({"_id": session.session_id})
    assert stored is not None and stored["revoked_at"] is not None
    assert_error(await client.get("/api/session", headers=session.headers), 401, "unauthorized")
    assert_error(await client.delete("/api/session", headers=session.headers), 401, "unauthorized")


async def test_revoking_one_session_does_not_affect_another(
    client: httpx.AsyncClient, create_session: CreateSession
) -> None:
    alice = await create_session()
    bob = await create_session()
    await client.delete("/api/session", headers=alice.headers)
    assert (await client.get("/api/session", headers=bob.headers)).status_code == 200


async def test_expired_session_is_rejected_even_before_ttl_cleanup(
    client: httpx.AsyncClient, create_session: CreateSession, db: Database
) -> None:
    session = await create_session()
    await db["sessions"].update_one(
        {"_id": session.session_id}, {"$set": {"expires_at": utc_now() - timedelta(seconds=1)}}
    )

    response = await client.get("/api/session", headers=session.headers)

    assert_error(response, 401, "session_expired")
    # The document still exists (TTL cleanup lags); access is denied regardless.
    assert await db["sessions"].count_documents({"_id": session.session_id}) == 1


async def test_token_is_never_accepted_from_the_query_string(
    client: httpx.AsyncClient, create_session: CreateSession
) -> None:
    session = await create_session()
    response = await client.get("/api/session", params={"token": session.token})
    assert_error(response, 401, "unauthorized")

"""The indexes the application depends on exist and are enforced."""

from datetime import timedelta

import pytest
from pymongo.errors import DuplicateKeyError

from app.db import EXPIRING_COLLECTIONS, Database, create_indexes
from app.repositories import Repositories
from app.schemas.common import new_id, utc_now
from app.schemas.documents import SessionDoc
from tests.factories import make_generation, make_profile


def keys_of(index: dict) -> list[tuple[str, int]]:
    return [(field, int(direction)) for field, direction in index["key"]]


async def test_every_expiring_collection_has_a_ttl_index_on_expires_at(db: Database) -> None:
    assert set(EXPIRING_COLLECTIONS) == {
        "sessions",
        "sources",
        "profiles",
        "evidence",
        "jobs",
        "generations",
        "rate_limits",
    }
    for name in EXPIRING_COLLECTIONS:
        indexes = await db[name].index_information()
        ttl = indexes["expires_at_ttl"]
        assert keys_of(ttl) == [("expires_at", 1)], name
        assert ttl["expireAfterSeconds"] == 0, name


async def test_unique_indexes_exist(db: Database) -> None:
    sessions = await db["sessions"].index_information()
    assert sessions["token_hash_unique"]["unique"] is True
    assert keys_of(sessions["token_hash_unique"]) == [("token_hash", 1)]

    profiles = await db["profiles"].index_information()
    assert profiles["owner_unique"]["unique"] is True
    assert keys_of(profiles["owner_unique"]) == [("owner_id", 1)]

    generations = await db["generations"].index_information()
    assert generations["owner_idempotency_unique"]["unique"] is True
    assert keys_of(generations["owner_idempotency_unique"]) == [
        ("owner_id", 1),
        ("idempotency_key", 1),
    ]


async def test_query_indexes_exist(db: Database) -> None:
    evidence = await db["evidence"].index_information()
    assert keys_of(evidence["owner_profile_version"]) == [
        ("owner_id", 1),
        ("profile_id", 1),
        ("profile_version", 1),
    ]
    assert keys_of(evidence["owner_content_hash"]) == [("owner_id", 1), ("content_hash", 1)]
    for name in ("jobs", "generations"):
        indexes = await db[name].index_information()
        assert keys_of(indexes["owner_created"]) == [("owner_id", 1), ("created_at", -1)]
    sources = await db["sources"].index_information()
    assert keys_of(sources["owner"]) == [("owner_id", 1)]


async def test_creating_indexes_again_changes_nothing_and_keeps_data(db: Database) -> None:
    await db["jobs"].insert_one({"_id": "keep-me", "owner_id": "o", "expires_at": utc_now()})
    before = {name: await db[name].index_information() for name in EXPIRING_COLLECTIONS}

    await create_indexes(db)
    await create_indexes(db)

    after = {name: await db[name].index_information() for name in EXPIRING_COLLECTIONS}
    assert after == before
    assert await db["jobs"].count_documents({"_id": "keep-me"}) == 1


async def test_two_sessions_cannot_share_a_token_hash(repos: Repositories) -> None:
    now = utc_now()

    def session_with_hash(token_hash: str) -> SessionDoc:
        return SessionDoc(
            session_id=new_id(),
            token_hash=token_hash,
            owner_id=new_id(),
            created_at=now,
            expires_at=now + timedelta(hours=1),
        )

    await repos.sessions.insert(session_with_hash("same-hash"))
    with pytest.raises(DuplicateKeyError):
        await repos.sessions.insert(session_with_hash("same-hash"))


async def test_an_owner_cannot_have_two_profiles(repos: Repositories) -> None:
    assert await repos.profiles.create("owner-1", make_profile("owner-1")) is True
    with pytest.raises(DuplicateKeyError):
        await repos.profiles.insert("owner-1", make_profile("owner-1"))


async def test_an_idempotency_key_is_unique_per_owner_only(repos: Repositories) -> None:
    await repos.generations.insert("owner-1", make_generation("owner-1", idempotency_key="k-1"))
    with pytest.raises(DuplicateKeyError):
        await repos.generations.insert("owner-1", make_generation("owner-1", idempotency_key="k-1"))
    # Another owner may use the same key.
    await repos.generations.insert("owner-2", make_generation("owner-2", idempotency_key="k-1"))

"""Shared pytest fixtures.

Safety rules enforced here:

- Tests never read the real ``.env``: settings are built with
  ``_env_file=None`` and every relevant value is passed explicitly.
- The AI provider is always the deterministic fake, so tests cost nothing.
- The database name is generated per test session, must start with
  ``resume_tailor_test`` (checked before use and before dropping) and is
  dropped when the session ends. Collections are emptied after every test.

Fixtures for integration tests (all function-scoped unless noted):

    settings        Settings for the test app; override with ``make_settings``
    make_settings   factory: make_settings(quota_generation=1, ...)
    app             FastAPI app with its lifespan running (database connected)
    make_app        async factory for an extra app with other settings
    client          httpx.AsyncClient bound to ``app``
    db              the test database (PyMongo async)
    repos           Repositories(db)
    fake_provider   the FakeProvider instance used by ``app``
    create_session  async factory -> SessionHandle(token, headers, owner_id, ...)
    auth_headers    Authorization headers of one fresh session
"""

import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from pymongo import MongoClient

from app.config import TEST_DATABASE_PREFIX, Settings
from app.db import EXPIRING_COLLECTIONS, Database
from app.main import create_app
from app.providers.fake.provider import FakeProvider
from app.repositories import Repositories
from app.security import hash_token

# Tests talk to the local docker compose MongoDB unless TEST_MONGODB_URI says otherwise.
TEST_MONGODB_URI = os.environ.get("TEST_MONGODB_URI", "mongodb://127.0.0.1:27017")
TEST_ORIGIN = "http://127.0.0.1:5173"


def assert_test_database(name: str) -> None:
    """Refuse to touch any database that is not clearly a disposable test database."""
    if not name.startswith(TEST_DATABASE_PREFIX):
        raise RuntimeError(f"refusing to run tests against non-test database {name!r}")


def build_test_settings(**overrides: Any) -> Settings:
    """Settings that depend on nothing outside the test process.

    ``_env_file=None`` disables the .env file; constructor arguments take
    priority over environment variables, so the values below always win.
    """
    values: dict[str, Any] = {
        "app_env": "test",
        "ai_provider": "fake",
        "openai_api_key": None,
        "mongodb_uri": TEST_MONGODB_URI,
        "mongodb_database": f"{TEST_DATABASE_PREFIX}_unit",
        "mongodb_server_selection_timeout_ms": 3000,
        "cors_origins": [TEST_ORIGIN],
        "trust_proxy_headers": False,
        "ip_hash_salt": "test-salt",
        # High enough that ordinary tests never hit it; rate-limit tests lower it.
        "session_create_limit_per_hour": 10_000,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture(scope="session")
def test_database_name() -> Iterator[str]:
    """A unique database name for this test session; dropped afterwards."""
    name = f"{TEST_DATABASE_PREFIX}_{uuid.uuid4().hex[:12]}"
    assert_test_database(name)
    yield name
    assert_test_database(name)
    with MongoClient(TEST_MONGODB_URI, serverSelectionTimeoutMS=3000) as client:
        client.drop_database(name)


@pytest.fixture
def make_settings(test_database_name: str) -> Callable[..., Settings]:
    def _make(**overrides: Any) -> Settings:
        settings = build_test_settings(mongodb_database=test_database_name, **overrides)
        assert_test_database(settings.mongodb_database)
        return settings

    return _make


@pytest.fixture
def settings(make_settings: Callable[..., Settings]) -> Settings:
    return make_settings()


async def _empty_collections(db: Database) -> None:
    for name in EXPIRING_COLLECTIONS:
        await db[name].delete_many({})


@pytest.fixture
async def make_app() -> AsyncIterator[Callable[[Settings], Awaitable[FastAPI]]]:
    """Async factory: ``other_app = await make_app(make_settings(...))``.

    Each app is started (lifespan entered); after the test its collections are
    emptied and it is shut down. httpx's ASGITransport does not run lifespan
    events, so that is done here.
    """
    created: list[FastAPI] = []
    async with AsyncExitStack() as stack:

        async def _make(app_settings: Settings) -> FastAPI:
            assert_test_database(app_settings.mongodb_database)
            application = create_app(app_settings)
            await stack.enter_async_context(application.router.lifespan_context(application))
            created.append(application)
            return application

        yield _make

        for application in created:
            # indexes_ready is False only when the database was never reachable.
            if application.state.mongo.indexes_ready:
                await _empty_collections(application.state.mongo.db)


@pytest.fixture
async def app(settings: Settings, make_app: Callable[[Settings], Awaitable[FastAPI]]) -> FastAPI:
    return await make_app(settings)


def client_for(application: FastAPI) -> httpx.AsyncClient:
    """An HTTP client that calls the app in-process (no network, no server)."""
    transport = httpx.ASGITransport(app=application)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with client_for(app) as http_client:
        yield http_client


@pytest.fixture
def db(app: FastAPI) -> Database:
    return app.state.mongo.db


@pytest.fixture
def repos(db: Database) -> Repositories:
    return Repositories(db)


@pytest.fixture
def fake_provider(app: FastAPI) -> FakeProvider:
    provider = app.state.provider
    assert isinstance(provider, FakeProvider)
    return provider


@dataclass(frozen=True)
class SessionHandle:
    """A session created through the API, plus what tests need to act as it."""

    token: str
    headers: dict[str, str]
    owner_id: str
    session_id: str
    expires_at: datetime


@pytest.fixture
def create_session(
    client: httpx.AsyncClient, db: Database
) -> Callable[[], Awaitable[SessionHandle]]:
    """Async factory creating a new anonymous session via POST /api/sessions.
    Call it twice to get two isolated visitors."""

    async def _create() -> SessionHandle:
        response = await client.post("/api/sessions")
        assert response.status_code == 201, response.text
        token = response.json()["token"]
        stored = await db["sessions"].find_one({"token_hash": hash_token(token)})
        return SessionHandle(
            token=token,
            headers={"Authorization": f"Bearer {token}"},
            owner_id=stored["owner_id"],
            session_id=stored["_id"],
            expires_at=stored["expires_at"],
        )

    return _create


@pytest.fixture
async def auth_headers(create_session: Callable[[], Awaitable[SessionHandle]]) -> dict[str, str]:
    return (await create_session()).headers

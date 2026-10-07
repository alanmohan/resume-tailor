"""MongoDB access: client lifecycle, collection names and index creation.

The client is PyMongo's native asyncio client (AsyncMongoClient), so database
calls never block the event loop. It is created inside the FastAPI lifespan
and stored on ``app.state.mongo``.

Startup does not require the database: if it is unreachable the app still
starts (so /healthz answers), /readyz reports 503 and index creation is retried
on the next readiness check or the next request that needs the database.
"""

import asyncio
import logging
from typing import Any

from fastapi import Request
from pymongo import ASCENDING, DESCENDING, AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import PyMongoError

from app.config import Settings
from app.logging_config import log_event

logger = logging.getLogger(__name__)

SESSIONS = "sessions"
SOURCES = "sources"
PROFILES = "profiles"
EVIDENCE = "evidence"
JOBS = "jobs"
GENERATIONS = "generations"
RATE_LIMITS = "rate_limits"

# Collections holding a visitor's data; "Clear my data" empties all of them.
OWNED_COLLECTIONS = (SOURCES, PROFILES, EVIDENCE, JOBS, GENERATIONS)
# Every document in these collections has an ``expires_at`` date and a TTL index.
EXPIRING_COLLECTIONS = (SESSIONS, *OWNED_COLLECTIONS, RATE_LIMITS)

Database = AsyncDatabase[dict[str, Any]]


async def create_indexes(db: Database) -> None:
    """Create every index the application relies on.

    Safe to run on every start: creating an index that already exists with the
    same definition does nothing, and nothing is ever dropped here.
    """
    for name in EXPIRING_COLLECTIONS:
        # expireAfterSeconds=0: MongoDB removes a document once its expires_at has
        # passed. The cleanup job runs about once a minute, so the application also
        # checks expiry itself on every request.
        await db[name].create_index("expires_at", expireAfterSeconds=0, name="expires_at_ttl")

    await db[SESSIONS].create_index("token_hash", unique=True, name="token_hash_unique")
    await db[SOURCES].create_index([("owner_id", ASCENDING)], name="owner")
    # One active profile per session.
    await db[PROFILES].create_index("owner_id", unique=True, name="owner_unique")
    await db[EVIDENCE].create_index(
        [("owner_id", ASCENDING), ("profile_id", ASCENDING), ("profile_version", ASCENDING)],
        name="owner_profile_version",
    )
    # Supports the embedding cache lookup by content hash.
    await db[EVIDENCE].create_index(
        [("owner_id", ASCENDING), ("content_hash", ASCENDING)], name="owner_content_hash"
    )
    await db[JOBS].create_index(
        [("owner_id", ASCENDING), ("created_at", DESCENDING)], name="owner_created"
    )
    await db[GENERATIONS].create_index(
        [("owner_id", ASCENDING), ("created_at", DESCENDING)], name="owner_created"
    )
    # Makes a repeated Idempotency-Key find the first request's document
    # instead of starting a second paid generation.
    await db[GENERATIONS].create_index(
        [("owner_id", ASCENDING), ("idempotency_key", ASCENDING)],
        unique=True,
        name="owner_idempotency_unique",
    )


class Mongo:
    """The client, the application database and the index-creation state."""

    def __init__(self, settings: Settings) -> None:
        # Creating the client does not connect; the first operation does.
        self.client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
            settings.mongodb_uri.get_secret_value(),
            tz_aware=True,
            serverSelectionTimeoutMS=settings.mongodb_server_selection_timeout_ms,
            appname="resume-tailor",
        )
        self.db: Database = self.client[settings.mongodb_database]
        self.indexes_ready = False
        self._index_lock = asyncio.Lock()

    async def ensure_indexes(self) -> None:
        """Create the indexes once per process. Raises if the database is unreachable."""
        if self.indexes_ready:
            return
        async with self._index_lock:
            if not self.indexes_ready:
                await create_indexes(self.db)
                self.indexes_ready = True

    async def try_ensure_indexes(self) -> bool:
        """Like ensure_indexes, but reports failure instead of raising (used at startup)."""
        try:
            await self.ensure_indexes()
        except PyMongoError as error:
            log_event(
                logger, logging.WARNING, "index_setup_deferred", error_type=type(error).__name__
            )
            return False
        return True

    async def is_ready(self) -> bool:
        """True when the database answers a ping and the indexes exist."""
        try:
            await self.db.command("ping")
        except PyMongoError:
            return False
        return await self.try_ensure_indexes()

    async def close(self) -> None:
        await self.client.close()


async def get_db(request: Request) -> Database:
    """FastAPI dependency: the application database, with indexes guaranteed.

    If the database is down, PyMongo raises ConnectionFailure here and the
    error handler answers 503 database_unavailable.
    """
    mongo: Mongo = request.app.state.mongo
    await mongo.ensure_indexes()
    return mongo.db

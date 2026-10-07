"""Persistent counters for rate limits (the ``rate_limits`` collection).

Counters live in MongoDB rather than in process memory so limits survive
restarts and hold across several server processes.
"""

from datetime import datetime

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.db import RATE_LIMITS, Database


class CounterRepository:
    def __init__(self, db: Database) -> None:
        self._collection = db[RATE_LIMITS]

    async def increment(self, key: str, expires_at: datetime, amount: int = 1) -> int:
        """Atomically add ``amount`` to the counter ``key`` and return the new total.

        The first increment creates the counter (upsert) and stamps it with
        ``expires_at``; the TTL index removes it some time after that, which is
        how a time window resets without any cleanup job.
        """
        update = {"$inc": {"count": amount}, "$setOnInsert": {"expires_at": expires_at}}
        try:
            document = await self._increment_once(key, update)
        except DuplicateKeyError:
            # Two requests raced to create the same counter; the loser retries
            # and now simply increments the document the winner created.
            document = await self._increment_once(key, update)
        return document["count"]

    async def _increment_once(self, key: str, update: dict) -> dict:
        return await self._collection.find_one_and_update(
            {"_id": key}, update, upsert=True, return_document=ReturnDocument.AFTER
        )

    async def get(self, key: str) -> int:
        document = await self._collection.find_one({"_id": key})
        return document["count"] if document else 0

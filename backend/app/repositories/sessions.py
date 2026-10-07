"""Session storage. Sessions are looked up by the hash of the bearer token."""

from datetime import datetime

from app.db import SESSIONS, Database
from app.schemas.documents import SessionDoc


class SessionRepository:
    def __init__(self, db: Database) -> None:
        self._collection = db[SESSIONS]

    async def insert(self, session: SessionDoc) -> None:
        await self._collection.insert_one(session.to_mongo())

    async def find_by_token_hash(self, token_hash: str) -> SessionDoc | None:
        raw = await self._collection.find_one({"token_hash": token_hash})
        return SessionDoc.from_mongo(raw) if raw else None

    async def find_by_id(self, session_id: str) -> SessionDoc | None:
        raw = await self._collection.find_one({"_id": session_id})
        return SessionDoc.from_mongo(raw) if raw else None

    async def revoke(self, session_id: str, now: datetime) -> bool:
        """Atomically mark the session revoked. False if it already was (or is gone).

        The document is kept as a tombstone until its TTL so that requests
        already in flight can still see the revocation.
        """
        result = await self._collection.update_one(
            {"_id": session_id, "revoked_at": None}, {"$set": {"revoked_at": now}}
        )
        return result.modified_count == 1

    async def use_quota(self, session_id: str, operation: str, limit: int, now: datetime) -> bool:
        """Atomically count one attempt of ``operation`` if the session is active
        and has used fewer than ``limit`` attempts. False means nothing was counted.

        The check and the increment are one update, so concurrent requests
        cannot both slip under the limit. ``$not $gte`` also matches a counter
        that does not exist yet.
        """
        counter = f"quota.{operation}"
        result = await self._collection.update_one(
            {
                "_id": session_id,
                "revoked_at": None,
                "expires_at": {"$gt": now},
                counter: {"$not": {"$gte": limit}},
            },
            {"$inc": {counter: 1}},
        )
        return result.modified_count == 1

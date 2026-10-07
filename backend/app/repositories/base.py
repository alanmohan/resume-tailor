"""Shared behaviour of repositories for owner-scoped collections.

Isolation rule: every query in every repository includes ``owner_id`` in the
Mongo filter. The owner always comes from the authenticated session, never
from request input, so a document ID alone can never reach another visitor's
data: a non-owned ID behaves exactly like a missing one.
"""

from typing import Any, ClassVar

from app.db import Database
from app.schemas.documents import MongoDocument


class OwnedRepository[DocT: MongoDocument]:
    collection_name: ClassVar[str]
    document_model: ClassVar[type[MongoDocument]]

    def __init__(self, db: Database) -> None:
        self._collection = db[self.collection_name]

    def _to_mongo(self, owner_id: str, document: DocT) -> dict[str, Any]:
        """Serialise a document, refusing one that belongs to a different owner."""
        data = document.to_mongo()
        if data.get("owner_id") != owner_id:
            raise ValueError("document owner does not match the authenticated owner")
        return data

    def _from_mongo(self, raw: dict[str, Any]) -> DocT:
        return self.document_model.from_mongo(raw)  # type: ignore[return-value]

    async def insert(self, owner_id: str, document: DocT) -> None:
        await self._collection.insert_one(self._to_mongo(owner_id, document))

    async def get(self, owner_id: str, document_id: str) -> DocT | None:
        """The owner's document with this ID, or None if missing or not owned."""
        raw = await self._collection.find_one({"_id": document_id, "owner_id": owner_id})
        return self._from_mongo(raw) if raw else None

    async def delete_for_owner(self, owner_id: str) -> int:
        """Delete every document of this owner; returns how many were removed."""
        result = await self._collection.delete_many({"owner_id": owner_id})
        return result.deleted_count

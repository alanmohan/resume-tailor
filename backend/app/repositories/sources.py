"""Storage for the labelled source texts a visitor pasted."""

from typing import ClassVar

from pymongo import ASCENDING

from app.db import SOURCES
from app.repositories.base import OwnedRepository
from app.schemas.documents import SourceDoc


class SourceRepository(OwnedRepository[SourceDoc]):
    collection_name: ClassVar[str] = SOURCES
    document_model = SourceDoc

    async def list_for_owner(self, owner_id: str) -> list[SourceDoc]:
        """The owner's sources in the order they were submitted."""
        cursor = self._collection.find({"owner_id": owner_id}).sort("position", ASCENDING)
        return [self._from_mongo(raw) async for raw in cursor]

    async def replace_for_owner(self, owner_id: str, sources: list[SourceDoc]) -> None:
        """Replace all of the owner's sources (used when a profile is re-ingested)."""
        documents = [self._to_mongo(owner_id, source) for source in sources]
        await self._collection.delete_many({"owner_id": owner_id})
        if documents:
            await self._collection.insert_many(documents)

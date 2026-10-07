"""Storage for evidence records and their embedding vectors.

Vectors are large (1536 floats each), so every read states whether it needs
them; without vectors ``EvidenceDoc.embedding`` is None.
"""

from typing import Any, ClassVar

from pymongo import ASCENDING, UpdateOne

from app.db import EVIDENCE
from app.repositories.base import OwnedRepository
from app.schemas.documents import EvidenceDoc

_WITHOUT_VECTOR = {"embedding": 0}


class EvidenceRepository(OwnedRepository[EvidenceDoc]):
    collection_name: ClassVar[str] = EVIDENCE
    document_model = EvidenceDoc

    async def insert_many(self, owner_id: str, documents: list[EvidenceDoc]) -> None:
        if documents:
            await self._collection.insert_many(
                [self._to_mongo(owner_id, document) for document in documents]
            )

    async def get(self, owner_id: str, document_id: str) -> EvidenceDoc | None:
        """One evidence record without its vector, or None if missing or not owned."""
        raw = await self._collection.find_one(
            {"_id": document_id, "owner_id": owner_id}, _WITHOUT_VECTOR
        )
        return self._from_mongo(raw) if raw else None

    async def get_many(
        self, owner_id: str, evidence_ids: list[str], *, with_vectors: bool = False
    ) -> list[EvidenceDoc]:
        """The owner's records among ``evidence_ids`` (unknown and non-owned IDs
        are simply absent from the result)."""
        projection = None if with_vectors else _WITHOUT_VECTOR
        cursor = self._collection.find(
            {"owner_id": owner_id, "_id": {"$in": evidence_ids}}, projection
        ).sort("position", ASCENDING)
        return [self._from_mongo(raw) async for raw in cursor]

    async def list_for_version(
        self, owner_id: str, profile_id: str, profile_version: int, *, with_vectors: bool
    ) -> list[EvidenceDoc]:
        """All evidence of exactly one profile version, in creation order.
        Retrieval uses this so vectors of different versions are never mixed."""
        projection = None if with_vectors else _WITHOUT_VECTOR
        cursor = self._collection.find(
            {"owner_id": owner_id, "profile_id": profile_id, "profile_version": profile_version},
            projection,
        ).sort("position", ASCENDING)
        return [self._from_mongo(raw) async for raw in cursor]

    async def find_reusable_embeddings(
        self,
        owner_id: str,
        content_hashes: list[str],
        embedding_model: str,
        embedding_dimension: int,
    ) -> dict[str, list[float]]:
        """Embedding cache lookup: vectors this owner already paid for.

        Returns ``{content_hash: vector}`` for hashes that have an embedded
        record produced by the same model and dimension. Vectors from another
        model or size are never returned, so they cannot be mixed.
        """
        cursor = self._collection.find(
            {
                "owner_id": owner_id,
                "content_hash": {"$in": content_hashes},
                "embedding_model": embedding_model,
                "embedding_dimension": embedding_dimension,
                "embedding_status": "embedded",
            },
            {"content_hash": 1, "embedding": 1},
        )
        return {raw["content_hash"]: raw["embedding"] async for raw in cursor}

    async def set_embeddings(self, owner_id: str, vectors: dict[str, list[float]]) -> int:
        """Store vectors (``{evidence_id: vector}``) and mark those records embedded."""
        if not vectors:
            return 0
        operations = [
            UpdateOne(
                {"_id": evidence_id, "owner_id": owner_id},
                {"$set": {"embedding": vector, "embedding_status": "embedded"}},
            )
            for evidence_id, vector in vectors.items()
        ]
        result = await self._collection.bulk_write(operations, ordered=False)
        return result.matched_count

    async def mark_failed(self, owner_id: str, evidence_ids: list[str]) -> int:
        """Mark records whose embedding call failed so the state is visible and retryable."""
        result = await self._collection.update_many(
            {"owner_id": owner_id, "_id": {"$in": evidence_ids}},
            {"$set": {"embedding_status": "failed"}},
        )
        return result.modified_count

    async def count_by_status(
        self, owner_id: str, profile_id: str, profile_version: int
    ) -> dict[str, int]:
        """``{"pending": n, "embedded": n, "failed": n}`` for one profile version."""
        counts = {"pending": 0, "embedded": 0, "failed": 0}
        pipeline: list[dict[str, Any]] = [
            {
                "$match": {
                    "owner_id": owner_id,
                    "profile_id": profile_id,
                    "profile_version": profile_version,
                }
            },
            {"$group": {"_id": "$embedding_status", "count": {"$sum": 1}}},
        ]
        cursor = await self._collection.aggregate(pipeline)
        async for row in cursor:
            counts[row["_id"]] = row["count"]
        return counts

    async def delete_version(self, owner_id: str, profile_id: str, profile_version: int) -> int:
        """Remove the evidence of one profile version (before rebuilding it on a
        confirm retry). Look up reusable embeddings first: they live in these
        documents."""
        result = await self._collection.delete_many(
            {"owner_id": owner_id, "profile_id": profile_id, "profile_version": profile_version}
        )
        return result.deleted_count

    async def delete_versions_except(self, owner_id: str, keep_versions: list[int]) -> int:
        """Remove the owner's evidence of every profile version that is not in
        ``keep_versions``; returns how many records were removed. Each record
        carries a vector of about 20 kB, so versions nothing refers to any
        more must not pile up."""
        result = await self._collection.delete_many(
            {"owner_id": owner_id, "profile_version": {"$nin": keep_versions}}
        )
        return result.deleted_count

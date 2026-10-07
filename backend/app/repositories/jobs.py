"""Storage for analysed job descriptions."""

from typing import ClassVar

from pymongo import DESCENDING, ReturnDocument

from app.db import JOBS
from app.repositories.base import OwnedRepository
from app.schemas.documents import JobDoc

LIST_LIMIT = 100


class JobRepository(OwnedRepository[JobDoc]):
    collection_name: ClassVar[str] = JOBS
    document_model = JobDoc

    async def list_for_owner(self, owner_id: str, limit: int = LIST_LIMIT) -> list[JobDoc]:
        """The owner's jobs, newest first."""
        cursor = (
            self._collection.find({"owner_id": owner_id})
            .sort("created_at", DESCENDING)
            .limit(limit)
        )
        return [self._from_mongo(raw) async for raw in cursor]

    async def replace(self, owner_id: str, job: JobDoc, expected_version: int) -> JobDoc | None:
        """Optimistic update: store ``job`` only if the stored version still
        equals ``expected_version``; None on a mismatch or a missing/non-owned
        job. The caller sets the new ``job.version``."""
        raw = await self._collection.find_one_and_replace(
            {"_id": job.job_id, "owner_id": owner_id, "version": expected_version},
            self._to_mongo(owner_id, job),
            return_document=ReturnDocument.AFTER,
        )
        return self._from_mongo(raw) if raw else None

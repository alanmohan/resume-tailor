"""Storage for generated drafts, including the idempotent "claim" that
guarantees one paid generation per Idempotency-Key."""

from datetime import datetime, timedelta
from typing import ClassVar

from pymongo import DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.db import GENERATIONS
from app.repositories.base import OwnedRepository
from app.schemas.documents import GenerationDoc
from app.schemas.generations import GenerationError

LIST_LIMIT = 100
# A "running" record older than this is treated as failed (its process was
# probably restarted mid-request) and may be taken over by a retry.
RUNNING_STALE_AFTER = timedelta(minutes=5)


class GenerationRepository(OwnedRepository[GenerationDoc]):
    collection_name: ClassVar[str] = GENERATIONS
    document_model = GenerationDoc

    async def list_for_owner(self, owner_id: str, limit: int = LIST_LIMIT) -> list[GenerationDoc]:
        """The owner's drafts, newest first."""
        cursor = (
            self._collection.find({"owner_id": owner_id})
            .sort("created_at", DESCENDING)
            .limit(limit)
        )
        return [self._from_mongo(raw) async for raw in cursor]

    async def claim(
        self, owner_id: str, candidate: GenerationDoc, now: datetime
    ) -> tuple[GenerationDoc, bool]:
        """Reserve the right to run a generation for ``candidate.idempotency_key``.

        ``candidate`` must have status "running". Returns ``(document, claimed)``:

        - ``claimed`` True: the caller owns this run and must finish it with
          ``complete`` or ``fail``. Either the candidate was inserted (first use
          of the key), or an earlier attempt with the same key had failed or
          had been "running" for longer than RUNNING_STALE_AFTER and was
          atomically restarted (same generation_id, ``attempt`` + 1).
        - ``claimed`` False: another request already used the key. Inspect
          ``document.status``: "completed" -> return it without calling the
          provider; "running" -> answer 409 generation_in_progress.

        The unique index on (owner_id, idempotency_key) makes the insert the
        atomic decision point, so two simultaneous requests cannot both claim.
        """
        try:
            await self.insert(owner_id, candidate)
            return candidate, True
        except DuplicateKeyError:
            pass

        key_filter = {"owner_id": owner_id, "idempotency_key": candidate.idempotency_key}
        restartable = [
            {"status": "failed"},
            {"status": "running", "started_at": {"$lt": now - RUNNING_STALE_AFTER}},
        ]
        restarted = await self._collection.find_one_and_update(
            {**key_filter, "$or": restartable},
            {
                "$set": {
                    "status": "running",
                    "error": None,
                    "started_at": now,
                    "updated_at": now,
                    "job_id": candidate.job_id,
                    "job_version": candidate.job_version,
                    "job_title": candidate.job_title,
                    "company": candidate.company,
                    "profile_id": candidate.profile_id,
                    "profile_version": candidate.profile_version,
                    "provider_mode": candidate.provider_mode,
                    "model": candidate.model,
                },
                "$inc": {"attempt": 1},
            },
            return_document=ReturnDocument.AFTER,
        )
        if restarted:
            return self._from_mongo(restarted), True

        existing = await self._collection.find_one(key_filter)
        if existing is None:
            # The earlier document vanished between the two steps (its session
            # was cleared). Inserting again is safe: the post-write guard of the
            # caller removes it if the session is revoked.
            await self.insert(owner_id, candidate)
            return candidate, True
        return self._from_mongo(existing), False

    async def complete(self, owner_id: str, generation: GenerationDoc) -> GenerationDoc | None:
        """Store the finished draft (``generation.status`` set to "completed" by
        the caller) if this attempt still owns the running record. Returns None
        when it does not: the record was deleted, or a retry took the run over
        after it went stale, in which case this result is discarded."""
        raw = await self._collection.find_one_and_replace(
            {
                "_id": generation.generation_id,
                "owner_id": owner_id,
                "status": "running",
                "attempt": generation.attempt,
            },
            self._to_mongo(owner_id, generation),
            return_document=ReturnDocument.AFTER,
        )
        return self._from_mongo(raw) if raw else None

    async def fail(
        self,
        owner_id: str,
        generation_id: str,
        attempt: int,
        error: GenerationError,
        now: datetime,
    ) -> bool:
        """Mark this attempt failed so the same Idempotency-Key can be retried.
        ``error`` must hold a safe code and message, never provider payloads."""
        result = await self._collection.update_one(
            {"_id": generation_id, "owner_id": owner_id, "status": "running", "attempt": attempt},
            {"$set": {"status": "failed", "error": error.model_dump(), "updated_at": now}},
        )
        return result.modified_count == 1

    async def save(
        self, owner_id: str, generation: GenerationDoc, expected_revision: int
    ) -> GenerationDoc | None:
        """Optimistic update of a completed draft (edits, validation, regeneration):
        stored only if the revision still equals ``expected_revision``; None on a
        mismatch. The caller sets the new ``generation.revision``."""
        raw = await self._collection.find_one_and_replace(
            {
                "_id": generation.generation_id,
                "owner_id": owner_id,
                "revision": expected_revision,
            },
            self._to_mongo(owner_id, generation),
            return_document=ReturnDocument.AFTER,
        )
        return self._from_mongo(raw) if raw else None

    async def claim_regeneration_key(self, owner_id: str, generation_id: str, key: str) -> bool:
        """Atomically record a single-item regeneration key on a draft.

        True: first use, the caller may call the provider. False: the key was
        already used (or the draft does not exist), so return the stored draft
        without another provider call. Load the draft AFTER claiming, so the
        document later passed to ``save`` already contains the key.
        """
        result = await self._collection.update_one(
            {"_id": generation_id, "owner_id": owner_id, "regeneration_keys": {"$ne": key}},
            {"$push": {"regeneration_keys": key}},
        )
        return result.modified_count == 1

    async def release_regeneration_key(self, owner_id: str, generation_id: str, key: str) -> None:
        """Undo claim_regeneration_key after a failed regeneration so the same
        key can be retried."""
        await self._collection.update_one(
            {"_id": generation_id, "owner_id": owner_id}, {"$pull": {"regeneration_keys": key}}
        )

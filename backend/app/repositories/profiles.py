"""Storage for the single active profile of each session."""

from datetime import datetime
from typing import Any, ClassVar

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.db import PROFILES
from app.repositories.base import OwnedRepository
from app.schemas.documents import ProfileDoc
from app.schemas.profiles import IndexProgress, IndexState, ProfileStatus


class ProfileRepository(OwnedRepository[ProfileDoc]):
    collection_name: ClassVar[str] = PROFILES
    document_model = ProfileDoc

    async def get_for_owner(self, owner_id: str) -> ProfileDoc | None:
        """The owner's profile (there is at most one), or None."""
        raw = await self._collection.find_one({"owner_id": owner_id})
        return self._from_mongo(raw) if raw else None

    async def exists(self, owner_id: str) -> bool:
        return await self._collection.count_documents({"owner_id": owner_id}, limit=1) == 1

    async def create(self, owner_id: str, profile: ProfileDoc) -> bool:
        """Insert the owner's first profile. False if one already exists (the
        unique index on owner_id rejected the insert, e.g. two ingests raced)."""
        try:
            await self.insert(owner_id, profile)
        except DuplicateKeyError:
            return False
        return True

    async def replace(
        self, owner_id: str, profile: ProfileDoc, expected_version: int
    ) -> ProfileDoc | None:
        """Optimistic update: store ``profile`` only if the stored version still
        equals ``expected_version``. Returns the stored document, or None when
        someone else changed the profile first (the caller answers 409).

        The caller sets the new ``profile.version`` (normally expected_version + 1).
        """
        raw = await self._collection.find_one_and_replace(
            {"_id": profile.profile_id, "owner_id": owner_id, "version": expected_version},
            self._to_mongo(owner_id, profile),
            return_document=ReturnDocument.AFTER,
        )
        return self._from_mongo(raw) if raw else None

    async def set_index_state(
        self,
        owner_id: str,
        version: int,
        *,
        index_state: IndexState,
        index_progress: IndexProgress,
        now: datetime,
        index_error: str | None = None,
        indexed_version: int | None = None,
        status: ProfileStatus | None = None,
    ) -> ProfileDoc | None:
        """Record indexing progress without changing the profile version.

        Applies only while the stored version is still ``version``; returns None
        if the profile was edited meanwhile, so an indexing run for an old
        version can never mark a newer version as indexed. ``indexed_version``
        and ``status`` are left untouched unless given.
        """
        changes: dict[str, Any] = {
            "index_state": index_state,
            "index_progress": index_progress.model_dump(),
            "index_error": index_error,
            "updated_at": now,
        }
        if indexed_version is not None:
            changes["indexed_version"] = indexed_version
        if status is not None:
            changes["status"] = status
        raw = await self._collection.find_one_and_update(
            {"owner_id": owner_id, "version": version},
            {"$set": changes},
            return_document=ReturnDocument.AFTER,
        )
        return self._from_mongo(raw) if raw else None

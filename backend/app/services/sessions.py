"""Session lifecycle: create, clear ("Clear my data") and the post-write guard."""

from datetime import timedelta

from app.config import Settings
from app.errors import SessionExpired, Unauthorized
from app.repositories import Repositories
from app.schemas.common import ProviderMode, new_id, utc_now
from app.schemas.documents import QUOTA_OPERATIONS, SessionDoc
from app.schemas.sessions import DeletedCounts, Limits
from app.security import SessionContext, generate_token, hash_token


class SessionService:
    def __init__(
        self, repos: Repositories, settings: Settings, provider_mode: ProviderMode
    ) -> None:
        self._repos = repos
        self._settings = settings
        self.provider_mode = provider_mode

    def limits(self) -> Limits:
        return Limits(
            max_profile_chars=self._settings.max_profile_chars,
            max_job_chars=self._settings.max_job_chars,
            max_sources=self._settings.max_sources,
            max_requirements=self._settings.max_requirements,
            session_ttl_hours=self._settings.session_ttl_hours,
        )

    async def create(self) -> tuple[str, SessionDoc]:
        """Create a session and return ``(token, stored session)``.

        The token is returned to the visitor exactly once; only its hash is
        stored. ``owner_id`` is a separate random ID, so documents do not
        reference the session ID or anything derived from the token.
        """
        token = generate_token()
        now = utc_now()
        session = SessionDoc(
            session_id=new_id(),
            token_hash=hash_token(token),
            owner_id=new_id(),
            created_at=now,
            expires_at=now + timedelta(hours=self._settings.session_ttl_hours),
            quota={operation: 0 for operation in QUOTA_OPERATIONS},
        )
        await self._repos.sessions.insert(session)
        return token, session

    async def revoke_and_delete(self, session: SessionContext) -> DeletedCounts:
        """Handle "Clear my data": revoke first, then delete.

        Revoking first means the token stops working before any data is
        removed, and requests still in flight will notice (see
        guard_after_write) instead of writing data back after the deletion.
        """
        await self._repos.sessions.revoke(session.session_id, utc_now())
        return await self._repos.delete_all_for_owner(session.owner_id)

    async def guard_after_write(self, session: SessionContext) -> None:
        """Post-write guard: call right after a long-running operation's LAST write.

        An operation such as ingest or generation can spend many seconds in a
        provider call. If the visitor clears their data during that time, the
        operation would otherwise store its result after the deletion ran and
        the "deleted" data would be back. This guard re-reads the session
        after the write; if it was revoked or has expired, it deletes the
        owner's documents again and raises 401, so the request cannot return
        (or leave behind) the data.

        It is correct for either ordering: if the revocation happened before
        this check, the documents are deleted here; if it happens after this
        check, the delete pass of revoke_and_delete runs after our write and
        removes it.

        Usage in feature code (ingest, confirm, job analysis, generation,
        regeneration, validation):

            profile = await repos.profiles.replace(session.owner_id, updated, expected)
            await session_service.guard_after_write(session)
            return profile.to_api(sources)

        An operation with several writes (for example evidence batches followed
        by the profile update) calls the guard once, after the final write.
        """
        stored = await self._repos.sessions.find_by_id(session.session_id)
        revoked = stored is None or stored.revoked_at is not None
        expired = stored is not None and stored.expires_at <= utc_now()
        if not (revoked or expired):
            return
        await self._repos.delete_all_for_owner(session.owner_id)
        raise Unauthorized() if revoked else SessionExpired()

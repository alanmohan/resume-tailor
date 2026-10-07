"""Profile endpoints: ingest sources, review the draft, confirm and index it."""

from fastapi import APIRouter

from app.api.deps import (
    ProviderDep,
    QuotasDep,
    ReposDep,
    SessionDep,
    SessionServiceDep,
    SettingsDep,
)
from app.schemas.profiles import IngestRequest, Profile, ProfileConfirmRequest, ProfilePatchRequest
from app.services import indexing, ingestion, profile_service

router = APIRouter(prefix="/api", tags=["profiles"])


@router.post("/profiles/ingest")
async def ingest_profile(
    body: IngestRequest,
    session: SessionDep,
    repos: ReposDep,
    provider: ProviderDep,
    quotas: QuotasDep,
    sessions: SessionServiceDep,
    settings: SettingsDep,
) -> Profile:
    """Store the labelled source texts and return the extracted profile draft."""
    return await ingestion.ingest_profile(
        body,
        session=session,
        repos=repos,
        provider=provider,
        quotas=quotas,
        sessions=sessions,
        settings=settings,
    )


@router.get("/profile")
async def read_profile(session: SessionDep, repos: ReposDep) -> Profile:
    """The session's profile with its review and index status."""
    return await profile_service.get_profile(session=session, repos=repos)


@router.patch("/profile")
async def update_profile(
    body: ProfilePatchRequest, session: SessionDep, repos: ReposDep
) -> Profile:
    """Save the reviewed profile; 409 if it changed since ``expected_version``."""
    return await profile_service.patch_profile(body, session=session, repos=repos)


@router.post("/profile/confirm")
async def confirm_profile(
    body: ProfileConfirmRequest,
    session: SessionDep,
    repos: ReposDep,
    provider: ProviderDep,
    quotas: QuotasDep,
    sessions: SessionServiceDep,
    settings: SettingsDep,
) -> Profile:
    """Confirm the reviewed profile and build its evidence index."""
    return await indexing.confirm_profile(
        body,
        session=session,
        repos=repos,
        provider=provider,
        quotas=quotas,
        sessions=sessions,
        settings=settings,
    )

"""Job endpoints: analyse a job description and review its requirements."""

from fastapi import APIRouter

from app.api.deps import (
    ProviderDep,
    QuotasDep,
    ReposDep,
    SessionDep,
    SessionServiceDep,
    SettingsDep,
)
from app.schemas.jobs import Job, JobCreateRequest, JobList, JobPatchRequest
from app.services import job_service

router = APIRouter(prefix="/api", tags=["jobs"])


@router.post("/jobs", status_code=201)
async def create_job(
    body: JobCreateRequest,
    session: SessionDep,
    repos: ReposDep,
    provider: ProviderDep,
    quotas: QuotasDep,
    sessions: SessionServiceDep,
    settings: SettingsDep,
) -> Job:
    """Analyse a job description and return its editable requirements."""
    return await job_service.create_job(
        body,
        session=session,
        repos=repos,
        provider=provider,
        quotas=quotas,
        sessions=sessions,
        settings=settings,
    )


@router.get("/jobs")
async def list_jobs(session: SessionDep, repos: ReposDep) -> JobList:
    """The session's jobs, newest first."""
    return await job_service.list_jobs(session=session, repos=repos)


@router.get("/jobs/{job_id}")
async def read_job(job_id: str, session: SessionDep, repos: ReposDep) -> Job:
    return await job_service.get_job(job_id, session=session, repos=repos)


@router.patch("/jobs/{job_id}")
async def update_job(
    job_id: str, body: JobPatchRequest, session: SessionDep, repos: ReposDep, settings: SettingsDep
) -> Job:
    """Save the reviewed requirements; 409 if the job changed since ``expected_version``."""
    return await job_service.patch_job(
        job_id, body, session=session, repos=repos, settings=settings
    )

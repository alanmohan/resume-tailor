"""Anonymous session endpoints: create, inspect and "Clear my data"."""

from fastapi import APIRouter, Request

from app.api.deps import QuotasDep, ReposDep, SessionDep, SessionServiceDep, SettingsDep
from app.ratelimit import client_ip
from app.schemas.sessions import SessionCreated, SessionDeleted, SessionInfo

router = APIRouter(prefix="/api", tags=["sessions"])


@router.post("/sessions", status_code=201)
async def create_session(
    request: Request, quotas: QuotasDep, sessions: SessionServiceDep, settings: SettingsDep
) -> SessionCreated:
    """Issue an anonymous bearer token. This is the only /api route without
    authentication, so it is rate limited per client IP."""
    await quotas.check_session_creation(client_ip(request, settings.trust_proxy_headers))
    token, session = await sessions.create()
    return SessionCreated(
        token=token,
        expires_at=session.expires_at,
        provider_mode=sessions.provider_mode,
        limits=sessions.limits(),
    )


@router.get("/session")
async def read_session(
    session: SessionDep, sessions: SessionServiceDep, repos: ReposDep
) -> SessionInfo:
    return SessionInfo(
        expires_at=session.expires_at,
        provider_mode=sessions.provider_mode,
        limits=sessions.limits(),
        has_profile=await repos.profiles.exists(session.owner_id),
    )


@router.delete("/session")
async def delete_session(session: SessionDep, sessions: SessionServiceDep) -> SessionDeleted:
    """Revoke the token, then delete everything this session owns."""
    counts = await sessions.revoke_and_delete(session)
    return SessionDeleted(deleted_counts=counts)

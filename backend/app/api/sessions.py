"""Anonymous session endpoints: create, inspect and "Clear my data"."""

import logging

from fastapi import APIRouter, Request

from app.api.deps import (
    ClearableSessionDep,
    QuotasDep,
    ReposDep,
    SessionDep,
    SessionServiceDep,
    SettingsDep,
)
from app.logging_config import log_event
from app.ratelimit import client_address
from app.schemas.sessions import SessionCreated, SessionDeleted, SessionInfo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["sessions"])


@router.post("/sessions", status_code=201)
async def create_session(
    request: Request, quotas: QuotasDep, sessions: SessionServiceDep, settings: SettingsDep
) -> SessionCreated:
    """Issue an anonymous bearer token. This is the only /api route without
    authentication, so it is rate limited per client IP."""
    client = client_address(request, settings)
    # Only where the address was read from is logged, never the address: it
    # shows on a live deployment that the rate limit keys on a platform header.
    log_event(logger, logging.INFO, "session_create", client_ip_source=client.source)
    await quotas.check_session_creation(client.ip)
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
async def delete_session(
    session: ClearableSessionDep, sessions: SessionServiceDep
) -> SessionDeleted:
    """Revoke the token, then delete everything this session owns. May be
    sent again with the same token, for example after a database error."""
    counts = await sessions.revoke_and_delete(session)
    return SessionDeleted(deleted_counts=counts)

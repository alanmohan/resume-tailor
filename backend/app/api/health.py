"""Liveness and readiness probes (not under /api, no authentication)."""

from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.config import Settings
from app.db import Mongo
from app.schemas.common import ProviderMode
from app.schemas.sessions import Limits
from app.services.sessions import limits_of

router = APIRouter(tags=["health"])


class Health(BaseModel):
    status: Literal["ok"] = "ok"


class ReadinessChecks(BaseModel):
    database: Literal["ok", "unavailable"]
    provider: Literal["configured", "not_configured"]


class Readiness(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: ReadinessChecks
    provider_mode: ProviderMode
    # Reported here, without a session, so the Start page can show and apply
    # the real limits before the visitor has submitted anything.
    limits: Limits


@router.get("/healthz")
async def healthz() -> Health:
    """Liveness: the process is up. Touches neither configuration nor the
    database, so Render's health check passes even while the database is down."""
    return Health()


@router.get("/readyz", response_model=Readiness, responses={503: {"model": Readiness}})
async def readyz(request: Request) -> JSONResponse:
    """Readiness: the database answers a ping (and has its indexes) and the AI
    provider is configured. The provider is only checked for configuration; no
    billable call is made."""
    settings: Settings = request.app.state.settings
    mongo: Mongo = request.app.state.mongo
    checks = ReadinessChecks(
        database="ok" if await mongo.is_ready() else "unavailable",
        provider="configured" if settings.provider_configured else "not_configured",
    )
    ready = checks.database == "ok" and checks.provider == "configured"
    body = Readiness(
        status="ready" if ready else "not_ready",
        checks=checks,
        provider_mode=request.app.state.provider.mode,
        limits=limits_of(settings),
    )
    return JSONResponse(body.model_dump(), status_code=200 if ready else 503)

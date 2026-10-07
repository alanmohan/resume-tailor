"""Generation endpoints: POST /generations, GET /generations,
GET/PATCH /generations/{generation_id}, POST .../validate and
POST .../items/{item_id}/regenerate. Included by app/main.py.

The routes only translate HTTP to GenerationService calls. ``session`` is the
first parameter everywhere, so a request without a valid token is answered
with 401 before anything else about it is examined.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Response

from app.api.deps import (
    IdempotencyKeyDep,
    ProviderDep,
    QuotasDep,
    ReposDep,
    SessionDep,
    SessionServiceDep,
    SettingsDep,
)
from app.schemas.generations import (
    Generation,
    GenerationCreateRequest,
    GenerationList,
    GenerationPatchRequest,
    RegenerateItemRequest,
)
from app.services.generation import GenerationService

router = APIRouter(prefix="/api", tags=["generations"])


def get_generation_service(
    repos: ReposDep,
    provider: ProviderDep,
    quotas: QuotasDep,
    sessions: SessionServiceDep,
    settings: SettingsDep,
) -> GenerationService:
    return GenerationService(repos, provider, quotas, sessions, settings)


GenerationServiceDep = Annotated[GenerationService, Depends(get_generation_service)]


@router.post("/generations", status_code=201)
async def create_generation(
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
    body: GenerationCreateRequest,
    service: GenerationServiceDep,
    response: Response,
) -> Generation:
    """Retrieve evidence and generate a tailored resume and cover letter.

    201 with the new draft. A repeated Idempotency-Key returns the stored
    draft with 200 and no second provider call, or 409 generation_in_progress
    while the first request is still running.
    """
    generation, created = await service.create(session, body.job_id, idempotency_key)
    if not created:
        response.status_code = 200
    return generation


@router.get("/generations")
async def list_generations(session: SessionDep, service: GenerationServiceDep) -> GenerationList:
    return await service.list_summaries(session)


@router.get("/generations/{generation_id}")
async def read_generation(
    generation_id: str, session: SessionDep, service: GenerationServiceDep
) -> Generation:
    return await service.get(session, generation_id)


@router.patch("/generations/{generation_id}")
async def patch_generation(
    generation_id: str,
    session: SessionDep,
    body: GenerationPatchRequest,
    service: GenerationServiceDep,
) -> Generation:
    """Save manual edits and coverage corrections; never regenerates text."""
    return await service.patch(session, generation_id, body)


@router.post("/generations/{generation_id}/validate")
async def validate_generation(
    generation_id: str, session: SessionDep, service: GenerationServiceDep
) -> Generation:
    """Revalidate user-edited statements against their evidence, text unchanged."""
    return await service.validate(session, generation_id)


@router.post("/generations/{generation_id}/items/{item_id}/regenerate")
async def regenerate_item(
    generation_id: str,
    item_id: str,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
    service: GenerationServiceDep,
    body: RegenerateItemRequest | None = None,
) -> Generation:
    """Rewrite one statement from the draft's stored evidence. The optional
    instruction is a style preference; it cannot override grounding."""
    instruction = body.instruction if body else None
    return await service.regenerate_item(
        session, generation_id, item_id, idempotency_key, instruction
    )

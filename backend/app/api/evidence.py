"""Evidence endpoint: the source text behind a citation."""

from fastapi import APIRouter

from app.api.deps import ReposDep, SessionDep
from app.errors import NotFound
from app.schemas.evidence import Evidence

router = APIRouter(prefix="/api", tags=["evidence"])


@router.get("/evidence/{evidence_id}")
async def read_evidence(evidence_id: str, session: SessionDep, repos: ReposDep) -> Evidence:
    """The exact supporting excerpt, its source and the role or project it
    belongs to. The lookup is scoped to the caller, so an ID belonging to
    another session answers 404 exactly like an unknown one. Embedding vectors
    are neither loaded nor returned."""
    evidence = await repos.evidence.get(session.owner_id, evidence_id)
    if evidence is None:
        raise NotFound("This evidence record was not found.")
    return evidence.to_api()

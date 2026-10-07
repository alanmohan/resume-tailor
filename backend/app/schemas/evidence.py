"""Response model for a single evidence record. Embedding vectors are never exposed."""

from pydantic import BaseModel, Field

from app.schemas.common import Provenance


class EvidenceSource(BaseModel):
    """The source a piece of evidence came from. ``source_id``, ``start`` and
    ``end`` are None for statements the user typed during profile review."""

    source_id: str | None = None
    label: str
    source_type: str
    start: int | None = None
    end: int | None = None


class EvidenceParent(BaseModel):
    """The role, project or other profile record the evidence belongs to."""

    record_id: str
    category: str
    title: str
    organization: str | None = None


class Evidence(BaseModel):
    evidence_id: str
    excerpt: str
    text: str
    category: str
    source: EvidenceSource
    provenance: Provenance
    parent: EvidenceParent | None = None
    tags: list[str] = Field(default_factory=list)
    profile_version: int

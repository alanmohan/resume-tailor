"""Request/response models for anonymous sessions."""

from typing import Literal

from pydantic import BaseModel

from app.schemas.common import IsoDateTime, ProviderMode


class Limits(BaseModel):
    """Input limits the frontend needs in order to validate before submitting."""

    max_profile_chars: int
    max_job_chars: int
    max_sources: int
    max_requirements: int
    session_ttl_hours: int


class SessionCreated(BaseModel):
    token: str
    expires_at: IsoDateTime
    provider_mode: ProviderMode
    limits: Limits


class SessionInfo(BaseModel):
    expires_at: IsoDateTime
    provider_mode: ProviderMode
    limits: Limits
    has_profile: bool


class DeletedCounts(BaseModel):
    sources: int = 0
    profiles: int = 0
    evidence: int = 0
    jobs: int = 0
    generations: int = 0


class SessionDeleted(BaseModel):
    deleted: Literal[True] = True
    deleted_counts: DeletedCounts

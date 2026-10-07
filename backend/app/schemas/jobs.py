"""Request/response models for job descriptions and their reviewed requirements.

The job description length limit (MAX_JOB_CHARS) and the number of
requirements (MAX_REQUIREMENTS) are settings, so the job service enforces them.
"""

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import IsoDateTime, optional_text, required_text

RequirementCategory = Literal[
    "skill", "experience", "education", "certification", "responsibility", "other"
]
Importance = Literal["required", "preferred"]

RequirementText = required_text(500)
OptionalJobField = optional_text(200)


class SourceSpan(BaseModel):
    """Supporting span: ``excerpt == job_description[start:end]``."""

    start: int
    end: int
    excerpt: str


class Requirement(BaseModel):
    requirement_id: str
    text: str
    category: RequirementCategory
    importance: Importance
    # True when the requirement is implied by the posting rather than stated in it.
    inferred: bool = False
    keywords: list[str] = Field(default_factory=list)
    source_span: SourceSpan | None = None
    user_edited: bool = False


class Job(BaseModel):
    job_id: str
    version: int
    company: str | None
    title: str | None
    description: str
    role_summary: str | None
    requirements: list[Requirement]
    created_at: IsoDateTime
    updated_at: IsoDateTime
    expires_at: IsoDateTime


class JobSummary(BaseModel):
    job_id: str
    title: str | None
    company: str | None
    version: int
    requirement_count: int
    created_at: IsoDateTime
    updated_at: IsoDateTime


class JobList(BaseModel):
    jobs: list[JobSummary]


# ---- Requests ------------------------------------------------------------------


class JobCreateRequest(BaseModel):
    # Not trimmed: requirement source spans are offsets into this exact text.
    description: str
    company: OptionalJobField = None
    title: OptionalJobField = None

    @field_validator("description")
    @classmethod
    def _description_not_blank(cls, description: str) -> str:
        if not description.strip():
            raise ValueError("Job description must not be empty")
        return description


class RequirementInput(BaseModel):
    # None means a requirement the user added; the server assigns the ID.
    requirement_id: str | None = None
    text: RequirementText
    category: RequirementCategory
    importance: Importance


class JobPatchRequest(BaseModel):
    """``company`` / ``title`` are optional: when a key is absent the stored value
    is kept (check ``model_fields_set``); an explicit null clears it."""

    expected_version: int
    company: OptionalJobField = None
    title: OptionalJobField = None
    requirements: list[RequirementInput]

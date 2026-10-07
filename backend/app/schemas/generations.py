"""Request/response models for tailored resume and cover-letter drafts."""

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import IsoDateTime, ProviderMode, optional_text, required_text
from app.schemas.profiles import Contact

GenerationStatus = Literal["running", "completed", "failed"]
CoverageStatus = Literal["supported", "partial", "missing", "uncertain"]
ValidationStatus = Literal[
    "supported", "needs_review", "unsupported", "user_edited", "not_applicable"
]
ValidationState = Literal["validated", "needs_revalidation"]
StaleReason = Literal["profile_changed", "job_changed"]

IDEMPOTENCY_KEY_MIN_LENGTH = 8
IDEMPOTENCY_KEY_MAX_LENGTH = 128

EditedText = required_text(1200)
OptionalInstruction = optional_text(300)
OptionalNote = optional_text(500)


class Claim(BaseModel):
    """One generated statement (bullet, summary line, skill or paragraph) and
    the evidence that supports it."""

    item_id: str
    text: str
    evidence_ids: list[str] = Field(default_factory=list)
    validation_status: ValidationStatus
    warnings: list[str] = Field(default_factory=list)
    user_edited: bool = False


class ResumeEntry(BaseModel):
    """A resume block for one confirmed profile record. heading, subheading,
    location and date_range are composed by the server verbatim from that
    record; the model only writes the bullets."""

    entry_id: str
    record_id: str
    category: str
    heading: str
    subheading: str | None = None
    location: str | None = None
    date_range: str | None = None
    bullets: list[Claim] = Field(default_factory=list)


class ResumeDocument(BaseModel):
    contact: Contact
    summary: list[Claim] = Field(default_factory=list)
    experience: list[ResumeEntry] = Field(default_factory=list)
    # Holds project, publication and achievement records.
    projects: list[ResumeEntry] = Field(default_factory=list)
    education: list[ResumeEntry] = Field(default_factory=list)
    certifications: list[ResumeEntry] = Field(default_factory=list)
    skills: list[Claim] = Field(default_factory=list)


class CoverLetter(BaseModel):
    paragraphs: list[Claim] = Field(default_factory=list)


class CoverageItem(BaseModel):
    requirement_id: str
    requirement_text: str
    importance: str
    status: CoverageStatus
    evidence_ids: list[str] = Field(default_factory=list)
    rationale: str
    user_corrected: bool = False
    note: str | None = None


class CoverageSummary(BaseModel):
    """assessed = supported + partial + missing (uncertain is counted separately).
    percent = round(100 * (supported + 0.5 * partial) / assessed, 1), or None
    when nothing was assessed."""

    supported: int = 0
    partial: int = 0
    missing: int = 0
    uncertain: int = 0
    assessed: int = 0
    percent: float | None = None


class ValidationSummary(BaseModel):
    state: ValidationState
    needs_review_count: int = 0
    unsupported_count: int = 0
    user_edited_count: int = 0
    validated_at: IsoDateTime | None = None


class OmittedClaim(BaseModel):
    section: str
    text: str
    reason: str


class UsageSummary(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    embedding_tokens: int = 0
    provider_calls: int = 0


class GenerationError(BaseModel):
    code: str
    message: str


class Generation(BaseModel):
    generation_id: str
    status: GenerationStatus
    error: GenerationError | None
    revision: int
    job_id: str
    job_version: int
    job_title: str | None
    company: str | None
    profile_id: str
    profile_version: int
    stale: bool
    stale_reasons: list[StaleReason]
    provider_mode: ProviderMode
    model: str
    retrieved_evidence_ids: list[str]
    resume: ResumeDocument | None
    cover_letter: CoverLetter | None
    coverage: list[CoverageItem]
    coverage_summary: CoverageSummary
    validation: ValidationSummary
    omitted_claims: list[OmittedClaim]
    warnings: list[str]
    usage: UsageSummary
    created_at: IsoDateTime
    updated_at: IsoDateTime
    expires_at: IsoDateTime


class GenerationSummary(BaseModel):
    generation_id: str
    job_id: str
    job_title: str | None
    company: str | None
    status: GenerationStatus
    stale: bool
    created_at: IsoDateTime


class GenerationList(BaseModel):
    generations: list[GenerationSummary]


# ---- Requests ------------------------------------------------------------------


class GenerationCreateRequest(BaseModel):
    job_id: str = Field(min_length=1, max_length=64)


class ItemEdit(BaseModel):
    item_id: str = Field(min_length=1, max_length=64)
    text: EditedText


class CoverageOverride(BaseModel):
    requirement_id: str = Field(min_length=1, max_length=64)
    status: CoverageStatus
    note: OptionalNote = None


class GenerationPatchRequest(BaseModel):
    expected_revision: int
    edits: list[ItemEdit] = Field(default_factory=list, max_length=200)
    coverage_overrides: list[CoverageOverride] = Field(default_factory=list, max_length=100)


class RegenerateItemRequest(BaseModel):
    instruction: OptionalInstruction = None

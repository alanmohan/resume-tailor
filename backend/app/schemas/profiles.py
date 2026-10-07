"""Request/response models for profile ingestion, review and confirmation.

Response models carry no length limits (they describe stored data); request
models do. Limits that depend on settings (number of sources, total profile
characters) are enforced by the ingestion service, not here.
"""

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import (
    IsoDateTime,
    Provenance,
    optional_text,
    require_valid_unicode,
    required_text,
)

SourceType = Literal["resume", "linkedin", "notes"]
RecordCategory = Literal[
    "employment", "education", "project", "publication", "achievement", "skill", "certification"
]
ProfileStatus = Literal["draft", "confirmed"]
IndexState = Literal["not_indexed", "indexing", "indexed", "failed"]
ConflictResolution = Literal["unresolved", "resolved", "dismissed"]

# Request-side text limits. They are generous because an extracted profile must
# always be saveable again after the user edits a single field.
SourceLabel = required_text(80)
TitleText = required_text(300)
OptionalLine = optional_text(300)
OptionalDate = optional_text(80)
OptionalSummary = optional_text(6000)
BulletText = required_text(4000)
SkillText = required_text(120)
LinkText = required_text(400)
OptionalNote = optional_text(500)

MAX_RECORDS = 300
MAX_BULLETS_PER_RECORD = 80
MAX_SKILLS_PER_RECORD = 300
MAX_LINKS = 20


# ---- Shared shapes -------------------------------------------------------------


class Contact(BaseModel):
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    links: list[str] = Field(default_factory=list)


class SourceRef(BaseModel):
    """Where a fact came from: ``excerpt == original_source_text[start:end]``."""

    source_id: str
    source_label: str
    start: int
    end: int
    excerpt: str


class ProfileBullet(BaseModel):
    bullet_id: str
    text: str
    source_ref: SourceRef | None = None
    provenance: Provenance
    needs_review: bool = False
    review_reasons: list[str] = Field(default_factory=list)


class ProfileRecord(BaseModel):
    """One reviewed fact group. Category "skill" is a skill group: title is the
    group label (for example "Languages") and ``skills`` holds the list."""

    record_id: str
    category: RecordCategory
    title: str
    organization: str | None = None
    location: str | None = None
    # Dates are kept exactly as written in the source; never normalised or guessed.
    start_date: str | None = None
    end_date: str | None = None
    summary: str | None = None
    bullets: list[ProfileBullet] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    source_ref: SourceRef | None = None
    provenance: Provenance
    needs_review: bool = False
    review_reasons: list[str] = Field(default_factory=list)


class ConflictValue(BaseModel):
    value: str
    source_ref: SourceRef | None = None


class Conflict(BaseModel):
    conflict_id: str
    field: str
    description: str
    record_ids: list[str] = Field(default_factory=list)
    values: list[ConflictValue] = Field(default_factory=list)
    resolution: ConflictResolution = "unresolved"
    note: str | None = None


class ProfileSourceSummary(BaseModel):
    source_id: str
    label: str
    source_type: str
    char_count: int
    revision: int


class ReviewSummary(BaseModel):
    needs_review_count: int
    unresolved_conflict_count: int


class IndexProgress(BaseModel):
    total: int = 0
    embedded: int = 0


class ProfileNotice(BaseModel):
    """A remark about the profile as a whole, not about one record: source
    text that was not captured, or a missing name, contact detail or education
    entry. It informs the user and never blocks confirmation. ``code`` is
    stable for clients; ``message`` is plain text."""

    code: str
    message: str


class Profile(BaseModel):
    profile_id: str
    version: int
    status: ProfileStatus
    index_state: IndexState
    indexed_version: int | None
    index_progress: IndexProgress
    index_error: str | None
    contact: Contact
    records: list[ProfileRecord]
    conflicts: list[Conflict]
    # Empty when there is nothing to remark; older clients can ignore the field.
    notices: list[ProfileNotice] = Field(default_factory=list)
    sources: list[ProfileSourceSummary]
    review_summary: ReviewSummary
    created_at: IsoDateTime
    updated_at: IsoDateTime
    expires_at: IsoDateTime


# ---- Requests ------------------------------------------------------------------


class SourceInput(BaseModel):
    label: SourceLabel
    source_type: SourceType
    # Not trimmed: the original text is stored untouched so source offsets stay valid.
    text: str

    @field_validator("text")
    @classmethod
    def _text_not_blank(cls, text: str) -> str:
        if not text.strip():
            raise ValueError("Source text must not be empty")
        return require_valid_unicode(text)


class IngestRequest(BaseModel):
    sources: list[SourceInput] = Field(min_length=1)


class ContactInput(BaseModel):
    name: OptionalLine = None
    email: OptionalLine = None
    phone: OptionalLine = None
    location: OptionalLine = None
    links: list[LinkText] = Field(default_factory=list, max_length=MAX_LINKS)


class ProfileBulletInput(BaseModel):
    # None means a bullet the user just added; the server assigns the ID.
    bullet_id: str | None = None
    text: BulletText


class ProfileRecordInput(BaseModel):
    # None means a record the user just added; the server assigns the ID.
    record_id: str | None = None
    category: RecordCategory
    title: TitleText
    organization: OptionalLine = None
    location: OptionalLine = None
    start_date: OptionalDate = None
    end_date: OptionalDate = None
    summary: OptionalSummary = None
    bullets: list[ProfileBulletInput] = Field(
        default_factory=list, max_length=MAX_BULLETS_PER_RECORD
    )
    skills: list[SkillText] = Field(default_factory=list, max_length=MAX_SKILLS_PER_RECORD)


class ConflictResolutionInput(BaseModel):
    conflict_id: str
    resolution: Literal["resolved", "dismissed"]
    note: OptionalNote = None


class ProfilePatchRequest(BaseModel):
    expected_version: int
    contact: ContactInput
    records: list[ProfileRecordInput] = Field(max_length=MAX_RECORDS)
    conflict_resolutions: list[ConflictResolutionInput] = Field(default_factory=list)


class ProfileConfirmRequest(BaseModel):
    expected_version: int

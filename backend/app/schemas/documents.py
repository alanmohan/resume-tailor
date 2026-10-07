"""Internal document models: the exact shape of what is stored in MongoDB.

Each model names one field as its identifier; that field is stored as Mongo's
``_id``. Datetimes are stored as BSON dates (timezone-aware UTC). Every owned
document carries ``owner_id`` and ``expires_at`` (equal to the session expiry),
which the TTL indexes use for cleanup. The ``to_api`` helpers are the single
place where stored documents become API responses, so internal fields such as
``owner_id`` or embedding vectors cannot leak by accident.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Any, ClassVar, Literal, Self

from pydantic import BaseModel, Field

from app.schemas.common import Provenance, ProviderMode
from app.schemas.evidence import Evidence, EvidenceParent, EvidenceSource
from app.schemas.generations import (
    CoverageItem,
    CoverageSummary,
    CoverLetter,
    Generation,
    GenerationError,
    GenerationStatus,
    GenerationSummary,
    OmittedClaim,
    ResumeDocument,
    StaleReason,
    UsageSummary,
    ValidationSummary,
)
from app.schemas.jobs import Job, JobSummary, Requirement
from app.schemas.profiles import (
    Conflict,
    Contact,
    IndexProgress,
    IndexState,
    Profile,
    ProfileNotice,
    ProfileRecord,
    ProfileSourceSummary,
    ProfileStatus,
    ReviewSummary,
    SourceType,
)

EmbeddingStatus = Literal["pending", "embedded", "failed"]

# Per-session operations that are limited by a quota (see app/ratelimit.py).
QuotaOperation = Literal[
    "ingest", "confirm", "job_analysis", "generation", "regeneration", "validation"
]
QUOTA_OPERATIONS: tuple[QuotaOperation, ...] = (
    "ingest",
    "confirm",
    "job_analysis",
    "generation",
    "regeneration",
    "validation",
)


class MongoDocument(BaseModel):
    """Base class mapping the model's identifier field to Mongo's ``_id``."""

    id_field: ClassVar[str]

    def to_mongo(self) -> dict[str, Any]:
        data = self.model_dump(mode="python")
        data["_id"] = data.pop(self.id_field)
        return data

    @classmethod
    def from_mongo(cls, raw: Mapping[str, Any]) -> Self:
        data = dict(raw)
        data[cls.id_field] = data.pop("_id")
        return cls.model_validate(data)


class SessionDoc(MongoDocument):
    id_field: ClassVar[str] = "session_id"

    session_id: str
    # sha256 of the bearer token; the token itself is never stored.
    token_hash: str
    owner_id: str
    created_at: datetime
    expires_at: datetime
    # Set by "Clear my data". A revoked session stays as a tombstone until TTL
    # so in-flight requests can still see that it was revoked.
    revoked_at: datetime | None = None
    # Attempts used per quota operation, e.g. {"ingest": 2, "generation": 1}.
    quota: dict[str, int] = Field(default_factory=dict)


class SourceDoc(MongoDocument):
    id_field: ClassVar[str] = "source_id"

    source_id: str
    owner_id: str
    profile_id: str
    label: str
    source_type: SourceType
    # The original text exactly as submitted; source offsets refer to this string.
    text: str
    revision: int
    content_hash: str
    char_count: int
    # Order in which the user submitted the sources (0, 1, 2, ...).
    position: int = 0
    created_at: datetime
    expires_at: datetime

    def to_summary(self) -> ProfileSourceSummary:
        return ProfileSourceSummary(
            source_id=self.source_id,
            label=self.label,
            source_type=self.source_type,
            char_count=self.char_count,
            revision=self.revision,
        )


class ProfileDoc(MongoDocument):
    id_field: ClassVar[str] = "profile_id"

    profile_id: str
    owner_id: str
    version: int
    status: ProfileStatus
    index_state: IndexState = "not_indexed"
    indexed_version: int | None = None
    index_progress: IndexProgress = Field(default_factory=IndexProgress)
    index_error: str | None = None
    contact: Contact = Field(default_factory=Contact)
    records: list[ProfileRecord] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    # Non-blocking remarks about the whole profile (app/services/ingestion.py).
    notices: list[ProfileNotice] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    expires_at: datetime

    def review_summary(self) -> ReviewSummary:
        flagged_records = sum(1 for record in self.records if record.needs_review)
        flagged_bullets = sum(
            1 for record in self.records for bullet in record.bullets if bullet.needs_review
        )
        unresolved = sum(1 for conflict in self.conflicts if conflict.resolution == "unresolved")
        return ReviewSummary(
            needs_review_count=flagged_records + flagged_bullets,
            unresolved_conflict_count=unresolved,
        )

    def to_api(self, sources: list[SourceDoc]) -> Profile:
        return Profile(
            profile_id=self.profile_id,
            version=self.version,
            status=self.status,
            index_state=self.index_state,
            indexed_version=self.indexed_version,
            index_progress=self.index_progress,
            index_error=self.index_error,
            contact=self.contact,
            records=self.records,
            conflicts=self.conflicts,
            notices=self.notices,
            sources=[source.to_summary() for source in sources],
            review_summary=self.review_summary(),
            created_at=self.created_at,
            updated_at=self.updated_at,
            expires_at=self.expires_at,
        )


class EvidenceDoc(MongoDocument):
    """One semantic unit of the confirmed profile (a bullet, a role summary, a
    skill group, ...) together with its embedding."""

    id_field: ClassVar[str] = "evidence_id"

    evidence_id: str
    owner_id: str
    profile_id: str
    profile_version: int
    # Order in which the evidence builder produced the records; gives listings
    # a stable order so ranking ties break the same way every time.
    position: int = 0
    record_id: str | None = None
    bullet_id: str | None = None
    source: EvidenceSource
    source_revision: int | None = None
    provenance: Provenance
    # The original wording shown to the user when a citation is opened.
    excerpt: str
    # Normalised text with a short role/project context prefix; this is what is embedded.
    text: str
    category: str
    tags: list[str] = Field(default_factory=list)
    parent: EvidenceParent | None = None
    embedding_model: str
    embedding_dimension: int
    content_hash: str
    # None when not embedded yet, or when the document was loaded without vectors.
    embedding: list[float] | None = None
    embedding_status: EmbeddingStatus = "pending"
    created_at: datetime
    expires_at: datetime

    def to_api(self) -> Evidence:
        return Evidence(
            evidence_id=self.evidence_id,
            excerpt=self.excerpt,
            text=self.text,
            category=self.category,
            source=self.source,
            provenance=self.provenance,
            parent=self.parent,
            tags=self.tags,
            profile_version=self.profile_version,
        )


class JobDoc(MongoDocument):
    id_field: ClassVar[str] = "job_id"

    job_id: str
    owner_id: str
    version: int
    company: str | None = None
    title: str | None = None
    description: str
    role_summary: str | None = None
    requirements: list[Requirement] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    expires_at: datetime

    def to_api(self) -> Job:
        return Job(
            job_id=self.job_id,
            version=self.version,
            company=self.company,
            title=self.title,
            description=self.description,
            role_summary=self.role_summary,
            requirements=self.requirements,
            created_at=self.created_at,
            updated_at=self.updated_at,
            expires_at=self.expires_at,
        )

    def to_summary(self) -> JobSummary:
        return JobSummary(
            job_id=self.job_id,
            title=self.title,
            company=self.company,
            version=self.version,
            requirement_count=len(self.requirements),
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


class GenerationDoc(MongoDocument):
    id_field: ClassVar[str] = "generation_id"

    generation_id: str
    owner_id: str
    # Unique per owner: a repeated request with the same key finds this document.
    idempotency_key: str
    status: GenerationStatus
    error: GenerationError | None = None
    # Incremented by every user edit, validation and regeneration (optimistic locking).
    revision: int = 1
    # Incremented each time a failed or abandoned run is restarted with the same key.
    attempt: int = 1
    started_at: datetime
    job_id: str
    job_version: int
    job_title: str | None = None
    company: str | None = None
    profile_id: str
    profile_version: int
    provider_mode: ProviderMode
    model: str
    retrieved_evidence_ids: list[str] = Field(default_factory=list)
    resume: ResumeDocument | None = None
    cover_letter: CoverLetter | None = None
    coverage: list[CoverageItem] = Field(default_factory=list)
    coverage_summary: CoverageSummary = Field(default_factory=CoverageSummary)
    validation: ValidationSummary = Field(
        default_factory=lambda: ValidationSummary(state="validated")
    )
    omitted_claims: list[OmittedClaim] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    usage: UsageSummary = Field(default_factory=UsageSummary)
    # Idempotency keys of single-item regenerations already applied to this draft.
    regeneration_keys: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    expires_at: datetime

    def to_api(self, stale_reasons: list[StaleReason]) -> Generation:
        """``stale_reasons`` is computed by the caller by comparing this draft's
        profile/job versions with the current ones."""
        return Generation(
            generation_id=self.generation_id,
            status=self.status,
            error=self.error,
            revision=self.revision,
            job_id=self.job_id,
            job_version=self.job_version,
            job_title=self.job_title,
            company=self.company,
            profile_id=self.profile_id,
            profile_version=self.profile_version,
            stale=bool(stale_reasons),
            stale_reasons=stale_reasons,
            provider_mode=self.provider_mode,
            model=self.model,
            retrieved_evidence_ids=self.retrieved_evidence_ids,
            resume=self.resume,
            cover_letter=self.cover_letter,
            coverage=self.coverage,
            coverage_summary=self.coverage_summary,
            validation=self.validation,
            omitted_claims=self.omitted_claims,
            warnings=self.warnings,
            usage=self.usage,
            created_at=self.created_at,
            updated_at=self.updated_at,
            expires_at=self.expires_at,
        )

    def to_summary(self, stale: bool) -> GenerationSummary:
        return GenerationSummary(
            generation_id=self.generation_id,
            job_id=self.job_id,
            job_title=self.job_title,
            company=self.company,
            status=self.status,
            stale=stale,
            created_at=self.created_at,
        )

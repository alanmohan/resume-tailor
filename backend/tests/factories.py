"""Builders for stored documents used by tests. All content is fictional.

Each builder returns a valid document with sensible defaults; pass keyword
arguments to override any field, e.g. ``make_job(owner_id, version=2)``.
"""

from datetime import timedelta
from typing import Any

from app.schemas.common import new_id, utc_now
from app.schemas.documents import EvidenceDoc, GenerationDoc, JobDoc, ProfileDoc, SourceDoc
from app.schemas.evidence import EvidenceParent, EvidenceSource
from app.schemas.jobs import Requirement
from app.schemas.profiles import ProfileRecord
from app.services.textutil import content_hash


def _timestamps() -> dict[str, Any]:
    now = utc_now()
    return {"created_at": now, "expires_at": now + timedelta(hours=24)}


def make_source(owner_id: str, profile_id: str = "profile-1", **overrides: Any) -> SourceDoc:
    text = overrides.pop("text", "Jordan Rivera\nData Engineer at Northwind Labs")
    values: dict[str, Any] = {
        "source_id": new_id(),
        "owner_id": owner_id,
        "profile_id": profile_id,
        "label": "Resume",
        "source_type": "resume",
        "text": text,
        "revision": 1,
        "content_hash": content_hash(text),
        "char_count": len(text),
        **_timestamps(),
    }
    values.update(overrides)
    return SourceDoc(**values)


def make_profile(owner_id: str, **overrides: Any) -> ProfileDoc:
    values: dict[str, Any] = {
        "profile_id": new_id(),
        "owner_id": owner_id,
        "version": 1,
        "status": "draft",
        "records": [
            ProfileRecord(
                record_id="record-1",
                category="employment",
                title="Data Engineer",
                organization="Northwind Labs",
                provenance="extracted",
            )
        ],
        "updated_at": utc_now(),
        **_timestamps(),
    }
    values.update(overrides)
    return ProfileDoc(**values)


def make_evidence(
    owner_id: str, profile_id: str = "profile-1", profile_version: int = 1, **overrides: Any
) -> EvidenceDoc:
    text = overrides.pop("text", "Data Engineer at Northwind Labs: Built Python data pipelines")
    values: dict[str, Any] = {
        "evidence_id": new_id(),
        "owner_id": owner_id,
        "profile_id": profile_id,
        "profile_version": profile_version,
        "record_id": "record-1",
        "bullet_id": "bullet-1",
        "source": EvidenceSource(
            source_id="source-1", label="Resume", source_type="resume", start=0, end=10
        ),
        "source_revision": 1,
        "provenance": "extracted",
        "excerpt": "Built Python data pipelines",
        "text": text,
        "category": "employment",
        "tags": ["python"],
        "parent": EvidenceParent(
            record_id="record-1",
            category="employment",
            title="Data Engineer",
            organization="Northwind Labs",
        ),
        "embedding_model": "fake-embedding-256",
        "embedding_dimension": 256,
        "content_hash": content_hash(text),
        **_timestamps(),
    }
    values.update(overrides)
    return EvidenceDoc(**values)


def make_job(owner_id: str, **overrides: Any) -> JobDoc:
    values: dict[str, Any] = {
        "job_id": new_id(),
        "owner_id": owner_id,
        "version": 1,
        "company": "Globex",
        "title": "Platform Engineer",
        "description": "We need Python and Docker experience.",
        "requirements": [
            Requirement(
                requirement_id="req-1",
                text="Python experience",
                category="skill",
                importance="required",
            )
        ],
        "updated_at": utc_now(),
        **_timestamps(),
    }
    values.update(overrides)
    return JobDoc(**values)


def make_generation(owner_id: str, **overrides: Any) -> GenerationDoc:
    now = utc_now()
    values: dict[str, Any] = {
        "generation_id": new_id(),
        "owner_id": owner_id,
        "idempotency_key": f"key-{new_id()}",
        "status": "running",
        "started_at": now,
        "job_id": "job-1",
        "job_version": 1,
        "job_title": "Platform Engineer",
        "company": "Globex",
        "profile_id": "profile-1",
        "profile_version": 1,
        "provider_mode": "fake",
        "model": "fake-llm-1",
        "updated_at": now,
        **_timestamps(),
    }
    values.update(overrides)
    return GenerationDoc(**values)

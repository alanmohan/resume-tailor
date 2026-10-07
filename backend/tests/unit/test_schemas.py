"""API models, request limits and the stored-document conversions."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import BaseModel, ValidationError

from app.schemas.common import IsoDateTime, iso_z, new_id, utc_now
from app.schemas.documents import (
    QUOTA_OPERATIONS,
    EvidenceDoc,
    GenerationDoc,
    JobDoc,
    ProfileDoc,
    SessionDoc,
    SourceDoc,
)
from app.schemas.evidence import EvidenceParent, EvidenceSource
from app.schemas.generations import (
    CoverageOverride,
    GenerationCreateRequest,
    GenerationPatchRequest,
    ItemEdit,
    RegenerateItemRequest,
)
from app.schemas.jobs import JobCreateRequest, JobPatchRequest, Requirement, RequirementInput
from app.schemas.profiles import (
    Conflict,
    IngestRequest,
    ProfileBullet,
    ProfilePatchRequest,
    ProfileRecord,
    ProfileRecordInput,
    SourceInput,
)

NOW = datetime(2026, 10, 7, 19, 30, 15, 123000, tzinfo=UTC)
LATER = NOW + timedelta(hours=24)

# ---- timestamps and IDs ------------------------------------------------------------


def test_iso_z_formats_utc_with_milliseconds_and_z() -> None:
    assert iso_z(NOW) == "2026-10-07T19:30:15.123Z"
    eastern = NOW.astimezone(timezone(timedelta(hours=-4)))
    assert iso_z(eastern) == "2026-10-07T19:30:15.123Z"
    assert iso_z(NOW.replace(tzinfo=None)) == "2026-10-07T19:30:15.123Z"


def test_iso_datetime_fields_serialise_to_z_strings_in_json_only() -> None:
    class Stamp(BaseModel):
        at: IsoDateTime
        maybe: IsoDateTime | None = None

    stamp = Stamp(at=NOW)
    assert stamp.model_dump(mode="json") == {"at": "2026-10-07T19:30:15.123Z", "maybe": None}
    # In Python mode the value stays a datetime, which is what MongoDB stores.
    assert stamp.model_dump()["at"] == NOW


def test_utc_now_is_timezone_aware_with_millisecond_precision() -> None:
    now = utc_now()
    assert now.tzinfo is UTC
    assert now.microsecond % 1000 == 0


def test_new_ids_are_32_hex_characters_and_unique() -> None:
    ids = {new_id() for _ in range(100)}
    assert len(ids) == 100
    assert all(len(value) == 32 and int(value, 16) >= 0 for value in ids)


# ---- request validation ------------------------------------------------------------


def test_source_label_is_trimmed_and_limited_to_80_characters() -> None:
    source = SourceInput(label="  My resume  ", source_type="resume", text="text")
    assert source.label == "My resume"
    with pytest.raises(ValidationError):
        SourceInput(label="x" * 81, source_type="resume", text="text")
    with pytest.raises(ValidationError):
        SourceInput(label="   ", source_type="resume", text="text")


def test_source_text_is_kept_untouched_but_must_not_be_blank() -> None:
    text = "  Jordan Rivera\r\n\tEngineer  "
    assert SourceInput(label="CV", source_type="resume", text=text).text == text
    with pytest.raises(ValidationError):
        SourceInput(label="CV", source_type="resume", text=" \n\t ")


def test_source_type_must_be_one_of_the_three_kinds() -> None:
    with pytest.raises(ValidationError):
        SourceInput(label="CV", source_type="pdf", text="text")


def test_ingest_requires_at_least_one_source() -> None:
    with pytest.raises(ValidationError):
        IngestRequest(sources=[])


def test_record_input_trims_text_and_turns_blank_optionals_into_none() -> None:
    record = ProfileRecordInput(
        category="employment",
        title="  Data Engineer ",
        organization="   ",
        start_date=" Jan 2021 ",
        bullets=[{"text": "  Built pipelines  "}],
        skills=[" Python "],
    )
    assert record.record_id is None
    assert record.title == "Data Engineer"
    assert record.organization is None
    assert record.start_date == "Jan 2021"
    assert record.bullets[0].bullet_id is None
    assert record.bullets[0].text == "Built pipelines"
    assert record.skills == ["Python"]


def test_record_input_rejects_blank_titles_and_bullets_and_unknown_categories() -> None:
    with pytest.raises(ValidationError):
        ProfileRecordInput(category="employment", title="  ")
    with pytest.raises(ValidationError):
        ProfileRecordInput(category="employment", title="Role", bullets=[{"text": ""}])
    with pytest.raises(ValidationError):
        ProfileRecordInput(category="hobby", title="Chess")


def test_profile_patch_accepts_only_resolved_or_dismissed_resolutions() -> None:
    body = {
        "expected_version": 2,
        "contact": {"name": "Jordan Rivera", "links": []},
        "records": [],
        "conflict_resolutions": [{"conflict_id": "c1", "resolution": "dismissed"}],
    }
    assert ProfilePatchRequest(**body).conflict_resolutions[0].note is None
    body["conflict_resolutions"] = [{"conflict_id": "c1", "resolution": "unresolved"}]
    with pytest.raises(ValidationError):
        ProfilePatchRequest(**body)


def test_job_description_is_kept_untouched_but_must_not_be_blank() -> None:
    job = JobCreateRequest(description="  We need Python.\n", company="  ", title=" Engineer ")
    assert job.description == "  We need Python.\n"
    assert job.company is None
    assert job.title == "Engineer"
    with pytest.raises(ValidationError):
        JobCreateRequest(description="   ")


def test_requirement_text_is_limited_to_500_characters() -> None:
    RequirementInput(text="x" * 500, category="skill", importance="required")
    with pytest.raises(ValidationError):
        RequirementInput(text="x" * 501, category="skill", importance="required")
    with pytest.raises(ValidationError):
        RequirementInput(text="Python", category="skill", importance="nice_to_have")


def test_job_patch_distinguishes_an_absent_title_from_an_explicit_null() -> None:
    absent = JobPatchRequest(expected_version=1, requirements=[])
    cleared = JobPatchRequest(expected_version=1, requirements=[], title=None)
    assert "title" not in absent.model_fields_set
    assert "title" in cleared.model_fields_set


def test_generation_edit_text_is_limited_to_1200_characters() -> None:
    ItemEdit(item_id="i1", text="x" * 1200)
    with pytest.raises(ValidationError):
        ItemEdit(item_id="i1", text="x" * 1201)
    with pytest.raises(ValidationError):
        ItemEdit(item_id="i1", text="   ")


def test_generation_request_models_have_sensible_defaults_and_enums() -> None:
    patch = GenerationPatchRequest(expected_revision=3)
    assert patch.edits == [] and patch.coverage_overrides == []
    assert CoverageOverride(requirement_id="r1", status="partial").note is None
    with pytest.raises(ValidationError):
        CoverageOverride(requirement_id="r1", status="great")
    with pytest.raises(ValidationError):
        GenerationCreateRequest(job_id="")
    assert RegenerateItemRequest().instruction is None
    with pytest.raises(ValidationError):
        RegenerateItemRequest(instruction="x" * 301)


# ---- stored documents --------------------------------------------------------------


def make_profile() -> ProfileDoc:
    flagged_bullet = ProfileBullet(
        bullet_id="b2",
        text="Led migration",
        provenance="extracted",
        needs_review=True,
        review_reasons=["source span not found"],
    )
    clean_bullet = ProfileBullet(bullet_id="b1", text="Built pipelines", provenance="extracted")
    records = [
        ProfileRecord(
            record_id="r1",
            category="employment",
            title="Data Engineer",
            provenance="extracted",
            needs_review=True,
            bullets=[clean_bullet, flagged_bullet],
        ),
        ProfileRecord(record_id="r2", category="skill", title="Skills", provenance="user_added"),
    ]
    conflicts = [
        Conflict(conflict_id="c1", field="start_date", description="Dates differ"),
        Conflict(
            conflict_id="c2", field="title", description="Titles differ", resolution="resolved"
        ),
    ]
    return ProfileDoc(
        profile_id="p1",
        owner_id="owner-1",
        version=3,
        status="draft",
        records=records,
        conflicts=conflicts,
        created_at=NOW,
        updated_at=NOW,
        expires_at=LATER,
    )


def make_source() -> SourceDoc:
    return SourceDoc(
        source_id="s1",
        owner_id="owner-1",
        profile_id="p1",
        label="Resume",
        source_type="resume",
        text="Jordan Rivera",
        revision=2,
        content_hash="abc",
        char_count=13,
        created_at=NOW,
        expires_at=LATER,
    )


def test_documents_store_their_id_as_mongo_id_and_round_trip() -> None:
    profile = make_profile()
    stored = profile.to_mongo()
    assert stored["_id"] == "p1"
    assert "profile_id" not in stored
    assert stored["expires_at"] == LATER  # a real datetime, so the TTL index works
    assert ProfileDoc.from_mongo(stored) == profile


def test_profile_to_api_adds_sources_and_review_summary_and_hides_owner() -> None:
    api = make_profile().to_api([make_source()])
    body = api.model_dump(mode="json")
    assert "owner_id" not in body
    assert body["review_summary"] == {"needs_review_count": 2, "unresolved_conflict_count": 1}
    assert body["sources"] == [
        {
            "source_id": "s1",
            "label": "Resume",
            "source_type": "resume",
            "char_count": 13,
            "revision": 2,
        }
    ]
    assert body["index_state"] == "not_indexed"
    assert body["indexed_version"] is None
    assert body["index_progress"] == {"total": 0, "embedded": 0}
    assert body["expires_at"] == "2026-10-08T19:30:15.123Z"
    assert "text" not in body["sources"][0]


def test_evidence_to_api_never_exposes_vectors_or_owner() -> None:
    evidence = EvidenceDoc(
        evidence_id="e1",
        owner_id="owner-1",
        profile_id="p1",
        profile_version=3,
        record_id="r1",
        bullet_id="b1",
        source=EvidenceSource(
            source_id="s1", label="Resume", source_type="resume", start=10, end=25
        ),
        source_revision=2,
        provenance="extracted",
        excerpt="Built pipelines",
        text="Data Engineer at Northwind: Built pipelines",
        category="employment",
        tags=["python"],
        parent=EvidenceParent(record_id="r1", category="employment", title="Data Engineer"),
        embedding_model="fake-embedding-256",
        embedding_dimension=256,
        content_hash="hash",
        embedding=[0.1, 0.2],
        embedding_status="embedded",
        created_at=NOW,
        expires_at=LATER,
    )
    body = evidence.to_api().model_dump(mode="json")
    assert set(body) == {
        "evidence_id",
        "excerpt",
        "text",
        "category",
        "source",
        "provenance",
        "parent",
        "tags",
        "profile_version",
    }
    assert body["source"] == {
        "source_id": "s1",
        "label": "Resume",
        "source_type": "resume",
        "start": 10,
        "end": 25,
    }


def test_job_to_api_and_summary() -> None:
    job = JobDoc(
        job_id="j1",
        owner_id="owner-1",
        version=2,
        company="Northwind",
        title="Engineer",
        description="We need Python.",
        requirements=[
            Requirement(requirement_id="q1", text="Python", category="skill", importance="required")
        ],
        created_at=NOW,
        updated_at=NOW,
        expires_at=LATER,
    )
    assert "owner_id" not in job.to_api().model_dump()
    assert job.to_summary().requirement_count == 1
    assert job.to_api().requirements[0].inferred is False


def make_generation() -> GenerationDoc:
    return GenerationDoc(
        generation_id="g1",
        owner_id="owner-1",
        idempotency_key="key-12345",
        status="running",
        started_at=NOW,
        job_id="j1",
        job_version=1,
        profile_id="p1",
        profile_version=3,
        provider_mode="fake",
        model="fake-llm-1",
        created_at=NOW,
        updated_at=NOW,
        expires_at=LATER,
    )


def test_generation_to_api_reports_staleness_and_hides_internal_fields() -> None:
    generation = make_generation()
    fresh = generation.to_api([]).model_dump(mode="json")
    stale = generation.to_api(["profile_changed"]).model_dump(mode="json")

    assert fresh["stale"] is False and fresh["stale_reasons"] == []
    assert stale["stale"] is True and stale["stale_reasons"] == ["profile_changed"]
    for internal in ("owner_id", "idempotency_key", "attempt", "started_at", "regeneration_keys"):
        assert internal not in fresh
    assert fresh["revision"] == 1
    assert fresh["resume"] is None and fresh["cover_letter"] is None
    assert fresh["coverage_summary"]["percent"] is None
    assert fresh["usage"] == {
        "input_tokens": 0,
        "output_tokens": 0,
        "embedding_tokens": 0,
        "provider_calls": 0,
    }
    assert generation.to_summary(stale=True).stale is True


def test_session_document_defaults() -> None:
    session = SessionDoc(
        session_id="s", token_hash="h", owner_id="o", created_at=NOW, expires_at=LATER
    )
    assert session.revoked_at is None
    assert session.quota == {}
    assert set(QUOTA_OPERATIONS) == {
        "ingest",
        "confirm",
        "job_analysis",
        "generation",
        "regeneration",
        "validation",
    }

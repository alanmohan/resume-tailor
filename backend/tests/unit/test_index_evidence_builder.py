"""Building evidence records from a reviewed profile (indexing.build_evidence):
semantic units, context, citations, contact stripping, tags, chunking and IDs."""

from typing import Any

from app.providers.base import LLMSource
from app.providers.fake.extraction_ops import extract_profile
from app.schemas.common import utc_now
from app.schemas.documents import EvidenceDoc, ProfileDoc, SourceDoc
from app.schemas.profiles import ProfileBullet, ProfileRecord
from app.services.indexing import (
    CHUNK_MAX_WORDS,
    CHUNK_MIN_WORDS,
    UNLOCATED_SOURCE_LABEL,
    USER_SOURCE_LABELS,
    USER_SOURCE_TYPE,
    build_evidence,
    statement_part,
)
from app.services.ingestion import build_draft, prepare_source
from app.services.textutil import content_hash, count_words
from tests.factories import make_profile, make_source

OWNER = "owner-1"

RESUME = (
    "Jordan Rivera\n"
    "jordan@example.com\n\n"
    "Experience\n"
    "Data  Engineer - Northwind Labs (Jan 2020 - Mar 2021)\n"
    "Owned the nightly data platform.\n"
    "- Built Python pipelines for 12 analysts\n"
    "- Wrote to jordan@example.com and https://example.com/docs about SQL tuning\n"
    "\n"
    "Projects\n"
    "Bird Feeder Camera - Hobby project\n"
    "- https://example.com/bird-feeder\n"
    "\n"
    "Skills\n"
    "Languages: Python, SQL, AWS (S3, EC2)\n"
)


def profile_from(text: str, **overrides: Any) -> tuple[ProfileDoc, list[SourceDoc]]:
    """Extract ``text`` with the fake provider, as the ingest endpoint would."""
    doc = make_source(OWNER, text=text, label="Resume", revision=3)
    source = prepare_source("S1", doc)
    extraction = extract_profile(
        [LLMSource(alias="S1", label="Resume", source_type="resume", text=source.normalized.text)]
    )
    draft = build_draft(extraction, [source])
    profile = make_profile(OWNER, records=draft.records, contact=draft.contact, **overrides)
    return profile, [doc]


def build(profile: ProfileDoc, sources: list[SourceDoc]) -> list[EvidenceDoc]:
    return build_evidence(
        profile,
        sources,
        embedding_model="fake-embedding-256",
        embedding_dimension=256,
        now=utc_now(),
    )


def test_one_evidence_record_per_semantic_unit_with_its_context() -> None:
    profile, sources = profile_from(RESUME)
    evidence = build(profile, sources)

    assert [item.text for item in evidence] == [
        "[Data Engineer at Northwind Labs] Jan 2020 - Mar 2021",
        "[Data Engineer at Northwind Labs] Owned the nightly data platform.",
        "[Data Engineer at Northwind Labs] Built Python pipelines for 12 analysts",
        "[Data Engineer at Northwind Labs] Wrote to and about SQL tuning",
        "[Project: Bird Feeder Camera (Hobby project)]",
        "[Skills (Languages)] Python, SQL, AWS (S3, EC2)",
    ]
    assert [item.position for item in evidence] == list(range(6))
    assert [item.category for item in evidence] == ["employment"] * 4 + ["project", "skill"]

    role = profile.records[0]
    overview, summary, bullet = evidence[0], evidence[1], evidence[2]
    assert overview.record_id == summary.record_id == bullet.record_id == role.record_id
    assert (overview.bullet_id, summary.bullet_id) == (None, None)
    assert bullet.bullet_id == role.bullets[0].bullet_id
    assert bullet.parent.model_dump() == {
        "record_id": role.record_id,
        "category": "employment",
        "title": "Data Engineer",
        "organization": "Northwind Labs",
    }


def test_statement_part_takes_the_context_prefix_off_again() -> None:
    profile, sources = profile_from(RESUME)

    assert [statement_part(item.text) for item in build(profile, sources)] == [
        "Jan 2020 - Mar 2021",
        "Owned the nightly data platform.",
        "Built Python pipelines for 12 analysts",
        "Wrote to and about SQL tuning",
        "",  # an overview without dates or location is only its context
        "Python, SQL, AWS (S3, EC2)",
    ]
    assert statement_part("Built [internal] tools") == "Built [internal] tools"


def test_extracted_evidence_cites_the_exact_span_of_the_original_text() -> None:
    profile, sources = profile_from(RESUME)
    evidence = build(profile, sources)
    original = sources[0].text

    for item in evidence:
        assert item.provenance == "extracted"
        assert item.source.source_id == sources[0].source_id
        assert (item.source.label, item.source.source_type) == ("Resume", "resume")
        assert item.source_revision == 3
        assert original[item.source.start : item.source.end] == item.excerpt

    # The excerpt is the original wording, double space included.
    assert evidence[0].excerpt == "Data  Engineer - Northwind Labs (Jan 2020 - Mar 2021)"
    # The summary paragraph is located on its own, not attributed to the header line.
    assert evidence[1].excerpt == "Owned the nightly data platform."
    assert evidence[2].excerpt == "Built Python pipelines for 12 analysts"


def test_contact_details_are_removed_from_the_embedded_text_only() -> None:
    profile, sources = profile_from(RESUME)
    statement = build(profile, sources)[3]

    assert "example.com" not in statement.text
    assert "@" not in statement.text
    # The citation still shows what the person actually wrote.
    assert "jordan@example.com" in statement.excerpt
    # A statement that is nothing but a link carries no evidence at all.
    assert not [item for item in build(profile, sources) if "bird-feeder" in item.excerpt]


def test_embedding_metadata_is_recorded_and_no_vector_is_set() -> None:
    profile, sources = profile_from(RESUME)
    for item in build(profile, sources):
        assert (item.embedding_model, item.embedding_dimension) == ("fake-embedding-256", 256)
        assert item.embedding is None
        assert item.embedding_status == "pending"
        assert item.content_hash == content_hash(item.text)
        assert (item.owner_id, item.profile_id) == (OWNER, profile.profile_id)
        assert item.profile_version == profile.version
        assert item.expires_at == profile.expires_at


def test_tags_are_the_profile_skills_named_in_the_statement() -> None:
    profile, sources = profile_from(RESUME)
    by_text = {item.text: item.tags for item in build(profile, sources)}

    assert by_text["[Data Engineer at Northwind Labs] Built Python pipelines for 12 analysts"] == [
        "python"
    ]
    assert by_text["[Data Engineer at Northwind Labs] Wrote to and about SQL tuning"] == ["sql"]
    assert by_text["[Data Engineer at Northwind Labs] Jan 2020 - Mar 2021"] == []
    # A skill with a note in brackets is also tagged by its plain name.
    assert by_text["[Skills (Languages)] Python, SQL, AWS (S3, EC2)"] == [
        "python",
        "sql",
        "aws (s3, ec2)",
        "aws",
    ]


def test_context_is_not_a_source_of_tags() -> None:
    record = ProfileRecord(
        record_id="r1",
        category="employment",
        title="Python Developer",
        organization="Acme",
        bullets=[ProfileBullet(bullet_id="b1", text="Ran the standup", provenance="user_added")],
        skills=["Python"],
        provenance="user_added",
    )
    evidence = build(make_profile(OWNER, records=[record]), [])
    assert [item.tags for item in evidence] == [[], []]


def test_user_statements_cite_the_review_instead_of_a_source() -> None:
    profile, sources = profile_from(RESUME)
    role = profile.records[0]
    role.bullets[0] = role.bullets[0].model_copy(
        update={"text": "Built Python pipelines for 20 analysts", "provenance": "user_edited"}
    )
    role.bullets.append(
        ProfileBullet(bullet_id="new", text="Wrote the runbook", provenance="user_added")
    )
    evidence = {item.bullet_id: item for item in build(profile, sources)}

    edited = evidence[role.bullets[0].bullet_id]
    assert edited.provenance == "user_edited"
    assert edited.excerpt == "Built Python pipelines for 20 analysts"
    assert edited.source.model_dump() == {
        "source_id": None,
        "label": USER_SOURCE_LABELS["user_edited"],
        "source_type": USER_SOURCE_TYPE,
        "start": None,
        "end": None,
    }
    assert edited.source_revision is None

    added = evidence["new"]
    assert added.provenance == "user_added"
    assert added.source.label == USER_SOURCE_LABELS["user_added"]
    assert added.text == "[Data Engineer at Northwind Labs] Wrote the runbook"


def test_extracted_statement_without_a_located_span_says_so() -> None:
    profile, sources = profile_from(RESUME)
    role = profile.records[0]
    role.bullets[0] = role.bullets[0].model_copy(update={"source_ref": None})

    item = next(e for e in build(profile, sources) if e.bullet_id == role.bullets[0].bullet_id)

    assert item.provenance == "extracted"
    assert item.source.label == UNLOCATED_SOURCE_LABEL
    assert (item.source.source_id, item.source.start, item.source.end) == (None, None, None)
    assert item.excerpt == "Built Python pipelines for 12 analysts"


def test_each_category_has_a_readable_context() -> None:
    def record(category: str, title: str, organization: str | None, **extra: Any) -> ProfileRecord:
        return ProfileRecord(
            record_id=f"{category}-1",
            category=category,
            title=title,
            organization=organization,
            provenance="user_added",
            **extra,
        )

    records = [
        record("employment", "Analyst", None, location="Leeds, UK"),
        record("education", "B.S. in Biology", "Lakeside University", end_date="2019"),
        record("certification", "First Aid", "Red Cross", start_date="Mar 2024"),
        record("publication", "Soil Notes", "Field Journal"),
        record("achievement", "Dean's List", None, start_date="2018", end_date="2019"),
        record("skill", "Skills", None, skills=["Excel"]),
        record("skill", "Tools", None),
    ]
    evidence = build(make_profile(OWNER, records=records), [])

    assert [item.text for item in evidence] == [
        "[Analyst] Leeds, UK",
        "[B.S. in Biology, Lakeside University] 2019",
        "[Certification: First Aid (Red Cross)] Mar 2024",
        "[Publication: Soil Notes (Field Journal)]",
        "[Achievement: Dean's List] 2018 - 2019",
        "[Skills] Excel",
        # A skill group without skills states nothing, so it yields no evidence.
    ]
    # Without a source excerpt the citation shows the overview in full, so it
    # names the record it is about.
    assert [item.excerpt for item in evidence] == [
        "Analyst: Leeds, UK",
        "B.S. in Biology, Lakeside University: 2019",
        "Certification: First Aid (Red Cross): Mar 2024",
        "Publication: Soil Notes (Field Journal)",
        "Achievement: Dean's List: 2018 - 2019",
        "Skills: Excel",
    ]


def test_long_text_is_split_at_sentence_boundaries_into_bounded_chunks() -> None:
    sentences = [
        f"Sentence {number} describes how the team migrated service {number} with care."
        for number in range(1, 61)
    ]
    paragraphs = [" ".join(sentences[start : start + 20]) for start in (0, 20, 40)]
    summary = "\n\n".join(paragraphs)
    text = f"Experience\nPlatform Engineer - Acme (2019 - 2023)\n{summary}\n- Kept it running\n"
    doc = make_source(OWNER, text=text, label="Notes", source_type="notes")
    record = ProfileRecord(
        record_id="r1",
        category="employment",
        title="Platform Engineer",
        organization="Acme",
        summary=summary,
        source_ref=prepare_source("S1", doc).locate("Platform Engineer - Acme (2019 - 2023)"),
        provenance="extracted",
    )
    assert count_words(summary) == 660

    evidence = build(make_profile(OWNER, records=[record]), [doc])
    chunks = evidence[1:]

    assert len(chunks) == 3
    prefix = "[Platform Engineer at Acme] "
    for chunk in chunks:
        words = count_words(chunk.text.removeprefix(prefix))
        assert CHUNK_MIN_WORDS <= words <= CHUNK_MAX_WORDS
        assert chunk.text.startswith(prefix)
        # Chunks end at a sentence boundary and keep the parent record.
        assert chunk.text.endswith("with care.")
        assert (chunk.record_id, chunk.parent.title) == ("r1", "Platform Engineer")
        # Each chunk cites its own span, not the whole paragraph.
        assert text[chunk.source.start : chunk.source.end] == chunk.excerpt
        assert chunk.excerpt.startswith("Sentence ")
    spans = [(chunk.source.start, chunk.source.end) for chunk in chunks]
    assert spans == sorted(spans)
    assert all(first[1] <= second[0] for first, second in zip(spans, spans[1:], strict=False))
    # Nothing is lost and nothing is repeated.
    rebuilt = " ".join(chunk.text.removeprefix(prefix) for chunk in chunks)
    assert rebuilt.split() == summary.split()


def test_text_within_the_limit_is_kept_as_one_chunk() -> None:
    statement = " ".join(["word"] * CHUNK_MAX_WORDS)
    record = ProfileRecord(
        record_id="r1",
        category="project",
        title="Big Project",
        bullets=[ProfileBullet(bullet_id="b1", text=statement, provenance="user_added")],
        provenance="user_added",
    )
    evidence = build(make_profile(OWNER, records=[record]), [])
    assert len(evidence) == 2
    assert evidence[1].excerpt == statement


def test_evidence_ids_are_stable_for_a_version_and_change_with_it() -> None:
    profile, sources = profile_from(RESUME)
    first = [item.evidence_id for item in build(profile, sources)]
    again = [item.evidence_id for item in build(profile, sources)]
    next_version = profile.model_copy(update={"version": profile.version + 1})
    other_owner = profile.model_copy(update={"owner_id": "owner-2"})

    assert first == again
    assert len(set(first)) == len(first)
    assert all(len(evidence_id) == 32 for evidence_id in first)
    assert not set(first) & {item.evidence_id for item in build(next_version, sources)}
    assert not set(first) & {item.evidence_id for item in build(other_owner, sources)}

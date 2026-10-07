"""EM-01: skills listed under a role, project or publication become evidence.

Before the fix only skill groups (category "skill") were indexed, so a skill
written under a role appeared in no evidence text and could be neither
retrieved nor cited. All content here is fictional.
"""

from app.schemas.common import utc_now
from app.schemas.documents import EvidenceDoc, ProfileDoc, SourceDoc
from app.schemas.profiles import ProfileBullet, ProfileRecord, SourceRef
from app.services.drafting import EvidenceIndex, check_skill
from app.services.indexing import (
    UNLOCATED_SOURCE_LABEL,
    USER_SOURCE_LABELS,
    build_evidence,
    statement_part,
)
from tests.factories import make_profile, make_source

OWNER = "owner-1"

RESUME = (
    "Experience\n"
    "Data Engineer - Northwind Labs (2020 - 2021)\n"
    "- Built nightly pipelines for 12 analysts\n"
    "Skills: Python, Airflow, BigQuery\n"
    "\n"
    "Analyst - Harbor Freight Co (2018 - 2020)\n"
    "- Wrote weekly reports\n"
    "Tools: Python, Airflow, BigQuery, Tableau\n"
)


def _ref(source: SourceDoc, quote: str) -> SourceRef:
    start = source.text.index(quote)
    return SourceRef(
        source_id=source.source_id,
        source_label=source.label,
        start=start,
        end=start + len(quote),
        excerpt=quote,
    )


def _role(source: SourceDoc, record_id: str, header: str, skills: list[str]) -> ProfileRecord:
    title, _, rest = header.partition(" - ")
    return ProfileRecord(
        record_id=record_id,
        category="employment",
        title=title,
        organization=rest.partition(" (")[0],
        skills=skills,
        source_ref=_ref(source, header),
        provenance="extracted",
    )


def _profile() -> tuple[ProfileDoc, SourceDoc]:
    source = make_source(OWNER, text=RESUME, revision=2)
    records = [
        _role(
            source,
            "r-engineer",
            "Data Engineer - Northwind Labs (2020 - 2021)",
            ["Python", "Airflow", "BigQuery"],
        ),
        _role(
            source,
            "r-analyst",
            "Analyst - Harbor Freight Co (2018 - 2020)",
            ["Python", "Tableau"],
        ),
    ]
    return make_profile(OWNER, records=records), source


def _build(profile: ProfileDoc, sources: list[SourceDoc]) -> list[EvidenceDoc]:
    return build_evidence(
        profile,
        sources,
        embedding_model="fake-embedding-256",
        embedding_dimension=256,
        now=utc_now(),
    )


def _skill_units(evidence: list[EvidenceDoc]) -> list[EvidenceDoc]:
    return [item for item in evidence if item.category == "skill"]


def test_skills_under_a_role_become_one_evidence_record_of_that_role() -> None:
    profile, source = _profile()
    engineer, analyst = _skill_units(_build(profile, [source]))

    assert engineer.text == "[Data Engineer at Northwind Labs] Skills: Python, Airflow, BigQuery"
    assert analyst.text == "[Analyst at Harbor Freight Co] Skills: Python, Tableau"
    assert statement_part(engineer.text) == "Skills: Python, Airflow, BigQuery"
    # It belongs to the role, so a bullet of that role may cite it.
    assert (engineer.record_id, engineer.bullet_id) == ("r-engineer", None)
    assert engineer.parent.model_dump() == {
        "record_id": "r-engineer",
        "category": "employment",
        "title": "Data Engineer",
        "organization": "Northwind Labs",
    }
    assert engineer.tags == ["python", "airflow", "bigquery"]
    assert analyst.tags == ["python", "tableau"]
    assert engineer.provenance == "extracted"


def test_skill_evidence_cites_the_line_the_skills_are_written_on() -> None:
    profile, source = _profile()
    engineer, analyst = _skill_units(_build(profile, [source]))

    assert engineer.excerpt == "Skills: Python, Airflow, BigQuery"
    assert analyst.excerpt == "Tools: Python, Airflow, BigQuery, Tableau"
    for item in (engineer, analyst):
        assert item.source.source_id == source.source_id
        assert source.text[item.source.start : item.source.end] == item.excerpt
        assert item.source_revision == 2


def test_the_skills_line_of_a_later_record_is_never_cited_for_an_earlier_one() -> None:
    """The engineer's skills are spread over the record here, so no single line
    of that record names them all. The analyst's "Tools" line does, but it is
    written under another employer and must not be shown as this role's text."""
    profile, source = _profile()
    profile.records[0].skills = ["Python", "Tableau"]

    engineer = _skill_units(_build(profile, [source]))[0]

    assert engineer.source.label == UNLOCATED_SOURCE_LABEL
    assert (engineer.source.source_id, engineer.source.start) == (None, None)
    assert engineer.excerpt == "Skills: Python, Tableau"


def test_skills_of_a_record_the_user_wrote_cite_the_review() -> None:
    record = ProfileRecord(
        record_id="p1",
        category="project",
        title="Bird Feeder Camera",
        bullets=[
            ProfileBullet(bullet_id="b1", text="Trained a classifier", provenance="user_added")
        ],
        skills=["OpenCV", "Raspberry Pi"],
        provenance="user_added",
    )
    unit = _skill_units(_build(make_profile(OWNER, records=[record]), []))[0]

    assert unit.text == "[Project: Bird Feeder Camera] Skills: OpenCV, Raspberry Pi"
    assert unit.excerpt == "Skills: OpenCV, Raspberry Pi"
    assert unit.provenance == "user_added"
    assert unit.source.label == USER_SOURCE_LABELS["user_added"]
    assert unit.tags == ["opencv", "raspberry pi"]


def test_records_without_skills_and_skill_groups_get_no_extra_record() -> None:
    records = [
        ProfileRecord(
            record_id="r1", category="employment", title="Analyst", provenance="user_added"
        ),
        ProfileRecord(
            record_id="s1",
            category="skill",
            title="Skills",
            skills=["Excel"],
            provenance="user_added",
        ),
    ]
    evidence = _build(make_profile(OWNER, records=records), [])

    assert [item.text for item in evidence] == ["[Analyst]", "[Skills] Excel"]


def test_a_skill_listed_only_under_a_role_can_be_cited() -> None:
    """The reported failure: such a skill was rejected as "not mentioned in
    your confirmed profile" because no evidence text contained it."""
    profile, source = _profile()
    evidence = _build(profile, [source])
    index = EvidenceIndex.of(evidence)

    evidence_ids, verdict = check_skill("Tableau", [], index)

    assert verdict.status == "supported"
    analyst = _skill_units(evidence)[1]
    assert evidence_ids == [analyst.evidence_id]

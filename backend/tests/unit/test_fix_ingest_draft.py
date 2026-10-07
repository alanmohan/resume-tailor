"""Review fixes in ingestion.build_draft: source text that was not captured
(EM-03), missing name, contact details or education (EM-08), publication status
and "Role:" lines (EM-11), contact details that differ between sources
(SPEC-03) and a date conflict listed twice (G-09).

All names and texts are invented. The extractions are written by hand, so the
tests cover what a real model did or could do; the fake provider is used only
where the test is about the fake itself."""

import json
from pathlib import Path

import pytest

from app.providers.base import LLMConflict, LLMConflictValue, LLMContactItem, LLMSource
from app.providers.fake.extraction_ops import extract_profile
from app.schemas.common import utc_now
from app.schemas.profiles import Contact, ProfilePatchRequest, ProfileRecord
from app.services.ingestion import (
    MAX_NOTICE_EXCERPT_CHARS,
    NOTICE_NO_CONTACT,
    NOTICE_NO_EDUCATION,
    NOTICE_NO_NAME,
    NOTICE_UNCAPTURED,
    Draft,
    build_draft,
    completeness_notices,
)
from app.services.profile_service import apply_patch
from tests.factories import make_profile
from tests.unit.test_ingest_draft import (
    HEADER,
    LINKEDIN,
    RESUME,
    draft_of,
    llm_bullet,
    llm_record,
    prepared,
)

PROFILES = Path(__file__).resolve().parents[2] / "fixtures" / "profiles"

PAPER_HEADER = "Faster Lookup for Tide Tables - Harbour Computing Workshop (2023)"
PAPER = (
    "Riley Park\n"
    "riley.park@example.com\n"
    "Education\n"
    "B.S. in Statistics - Lakeshore University (2015 - 2019)\n"
    "Publications\n"
    f"{PAPER_HEADER}\n"
    "We studied why lookups in printed tide tables are slow.\n"
    "Key results:\n"
    "- Cut the median lookup time from 9.1 s to 2.3 s on 1,200 test queries\n"
    "- Raised top-3 accuracy from 0.62 to 0.80\n"
    "- Released the index as an open-source library\n"
)
RESULT_LINES = [
    "Cut the median lookup time from 9.1 s to 2.3 s on 1,200 test queries",
    "Raised top-3 accuracy from 0.62 to 0.80",
    "Released the index as an open-source library",
]
DEGREE = llm_record(
    category="education",
    title="B.S. in Statistics",
    organization="Lakeshore University",
    start_date="2015",
    end_date="2019",
    header_quote="B.S. in Statistics - Lakeshore University (2015 - 2019)",
)
PAPER_CONTACT = [
    LLMContactItem(field="name", value="Riley Park", source="S1", quote="Riley Park"),
    LLMContactItem(
        field="email", value="riley.park@example.com", source="S1", quote="riley.park@example.com"
    ),
]


def paper(**overrides: object) -> object:
    values = {
        "category": "publication",
        "title": "Faster Lookup for Tide Tables",
        "organization": "Harbour Computing Workshop",
        "start_date": "2023",
        "end_date": None,
        "summary": "We studied why lookups in printed tide tables are slow.",
        "header_quote": PAPER_HEADER,
    }
    return llm_record(**(values | overrides))


def contact_item(field: str, value: str, source: str = "S1") -> LLMContactItem:
    return LLMContactItem(field=field, value=value, source=source, quote=value)


def uncaptured(draft: Draft) -> list[str]:
    return [notice.message for notice in draft.notices if notice.code == NOTICE_UNCAPTURED]


# ---- EM-03: source text that was not captured ---------------------------------------


def test_result_lines_the_model_skipped_under_a_publication_are_reported() -> None:
    """Seen with the real model: a publication came back without any bullet,
    and nothing told the user that its result list was gone."""
    draft = draft_of(
        [DEGREE, paper()], [prepared(PAPER, label="Career notes")], contact=PAPER_CONTACT
    )

    publication = draft.records[1]
    assert publication.bullets == []
    assert [notice.code for notice in draft.notices] == [NOTICE_UNCAPTURED]
    message = draft.notices[0].message
    assert message.startswith("3 lines of Career notes were not captured: ")
    for line in RESULT_LINES:
        assert f'"{line}"' in message
    # The notice informs; nothing is flagged for review and nothing blocks confirmation.
    assert publication.needs_review is False
    assert draft.conflicts == []


def test_completely_extracted_source_gives_no_notice() -> None:
    complete = paper(bullets=[llm_bullet(line) for line in RESULT_LINES])
    draft = draft_of([DEGREE, complete], [prepared(PAPER)], contact=PAPER_CONTACT)
    # Headings ("Publications") and the list label ("Key results:") are not content.
    assert draft.notices == []


def test_one_skipped_line_is_reported_in_the_singular() -> None:
    partial = paper(bullets=[llm_bullet(line) for line in RESULT_LINES[:2]])
    draft = draft_of([DEGREE, partial], [prepared(PAPER)], contact=PAPER_CONTACT)
    assert uncaptured(draft) == [
        '1 line of Resume was not captured: "Released the index as an open-source library". '
        "Add anything that matters to a record."
    ]


def test_notice_shows_three_excerpts_and_counts_the_rest() -> None:
    long_line = "Presented the measurements to the harbour board " + "in great detail " * 12
    extra = [f"Result number {n} was confirmed on a second data set" for n in range(1, 5)]
    text = PAPER + "\n".join([long_line.strip(), *extra]) + "\n"
    complete = paper(bullets=[llm_bullet(line) for line in RESULT_LINES])
    message = uncaptured(draft_of([DEGREE, complete], [prepared(text)], contact=PAPER_CONTACT))[0]

    assert message.startswith("5 lines of Resume were not captured: ")
    assert message.count('"; "') == 2
    assert "(and 2 more)" in message
    assert f'"{long_line[:MAX_NOTICE_EXCERPT_CHARS].rstrip()}..."' in message
    assert "Result number 3" not in message


def test_each_source_gets_its_own_notice_with_its_label() -> None:
    notes = "Experience\n" + HEADER + "\n- Mentored one intern\n- Ran the weekly data review\n"
    sources = [prepared(RESUME), prepared(notes, "S2", "Notes")]
    resume_role = llm_record(
        bullets=[
            llm_bullet("Built Python pipelines for 12 analysts"),
            llm_bullet("Cut report time by 40%"),
        ]
    )
    skills = llm_record(
        category="skill",
        title="Skills",
        organization=None,
        start_date=None,
        end_date=None,
        header_quote="Python, JavaScript, C",
        skills=["Python", "JavaScript", "C"],
    )
    notes_role = llm_record(source="S2", bullets=[llm_bullet("Mentored one intern", "S2")])
    draft = draft_of([resume_role, skills, notes_role], sources)
    assert uncaptured(draft) == [
        '1 line of Notes was not captured: "Ran the weekly data review". '
        "Add anything that matters to a record."
    ]


def test_paragraph_of_which_only_a_sentence_was_quoted_is_reported() -> None:
    paragraph = (
        "I rebuilt the reporting stack. It now serves 40 analysts across three offices, "
        "and the nightly load finishes before the first shift starts."
    )
    text = f"Experience\n{HEADER}\n{paragraph}\n"
    barely = llm_record(summary="I rebuilt the reporting stack.")
    whole = llm_record(summary=paragraph)
    assert len(uncaptured(draft_of([barely], [prepared(text)]))) == 1
    assert uncaptured(draft_of([whole], [prepared(text)])) == []


def test_lines_that_only_repeat_stored_facts_are_not_reported() -> None:
    text = (
        "Experience\n"
        "Data Engineer, Northwind Labs\n"
        "Jan 2020 - Mar 2021\n"
        "Key technologies: Python, Apache Airflow, dbt\n"
    )
    role = llm_record(
        header_quote="Data Engineer, Northwind Labs", skills=["Python", "Apache Airflow", "dbt"]
    )
    forgetful = llm_record(header_quote="Data Engineer, Northwind Labs", skills=["Python"])
    # The date line and the technology list hold nothing the record lacks ...
    assert uncaptured(draft_of([role], [prepared(text)])) == []
    # ... unless one of the listed technologies was dropped.
    assert uncaptured(draft_of([forgetful], [prepared(text)])) == [
        '1 line of Resume was not captured: "Key technologies: Python, Apache Airflow, dbt". '
        "Add anything that matters to a record."
    ]


def test_line_addressed_to_an_ai_system_is_never_quoted_in_a_notice() -> None:
    expected = json.loads((PROFILES / "injection_expected.json").read_text(encoding="utf-8"))
    text = (PROFILES / "injection_resume.txt").read_text(encoding="utf-8")
    source = prepared(text)
    extraction = extract_profile(
        [LLMSource(alias="S1", label="Resume", source_type="resume", text=source.normalized.text)]
    )
    draft = build_draft(extraction, [source])

    assert expected["injected_line"] in text
    shown = " ".join(notice.message for notice in draft.notices)
    for forbidden in (expected["canary"], *expected["must_not_claim"]):
        assert forbidden.casefold() not in shown.casefold()
    # The general summary paragraph is deliberately not a record; the user is told.
    assert len(uncaptured(draft)) == 1 and "Backend developer with two years" in shown


# ---- EM-08: missing name, contact details, education --------------------------------


def test_profile_without_name_contact_or_education_gets_three_notices() -> None:
    draft = draft_of([llm_record()], [prepared(f"Experience\n{HEADER}\n")])
    assert [notice.code for notice in draft.notices] == [
        NOTICE_NO_NAME,
        NOTICE_NO_CONTACT,
        NOTICE_NO_EDUCATION,
    ]
    assert all(notice.message for notice in draft.notices)
    # Nothing is guessed to fill the gap.
    assert draft.contact == Contact()


@pytest.mark.parametrize(
    ("contact", "categories", "codes"),
    [
        (Contact(name="Riley Park", email="riley.park@example.com"), ["education"], []),
        (Contact(name="Riley Park", phone="(614) 555-0142"), ["education"], []),
        (Contact(email="riley.park@example.com"), ["education"], [NOTICE_NO_NAME]),
        (Contact(name="Riley Park", location="Columbus, OH"), ["education"], [NOTICE_NO_CONTACT]),
        (Contact(name="Riley Park", email="r@example.com"), ["employment"], [NOTICE_NO_EDUCATION]),
    ],
)
def test_completeness_notices(contact: Contact, categories: list[str], codes: list[str]) -> None:
    records = [
        ProfileRecord(record_id=f"r{n}", category=category, title="T", provenance="extracted")
        for n, category in enumerate(categories)
    ]
    assert [notice.code for notice in completeness_notices(contact, records)] == codes


def test_edit_that_adds_the_missing_details_removes_their_notices() -> None:
    draft = draft_of(
        [llm_record()], [prepared(f"Experience\n{HEADER}\n- Did a thing nobody quoted\n")]
    )
    stored = make_profile(
        "owner-1", contact=draft.contact, records=draft.records, notices=draft.notices
    )
    assert [notice.code for notice in stored.notices] == [
        NOTICE_UNCAPTURED,
        NOTICE_NO_NAME,
        NOTICE_NO_CONTACT,
        NOTICE_NO_EDUCATION,
    ]
    role = stored.records[0]
    body = ProfilePatchRequest.model_validate(
        {
            "expected_version": stored.version,
            "contact": {"name": "Riley Park", "email": "riley.park@example.com"},
            "records": [
                {"record_id": role.record_id, "category": "employment", "title": role.title}
            ],
        }
    )
    updated = apply_patch(stored, body, utc_now())
    # What the sources hold did not change; what the profile lacks did.
    assert [notice.code for notice in updated.notices] == [NOTICE_UNCAPTURED, NOTICE_NO_EDUCATION]
    assert updated.notices[0] == stored.notices[0]


# ---- EM-11: publication status and "Role:" lines ------------------------------------


def test_publication_status_and_role_line_are_kept_as_written() -> None:
    header = "Faster Lookup for Tide Tables\nHarbour Computing Workshop. Accepted, to appear 2025."
    text = f"Publications\n{header}\nRole: First author\n"
    record = paper(
        start_date="Accepted, to appear 2025",
        summary=None,
        header_quote=header,
        bullets=[llm_bullet("Role: First author")],
    )
    stored = draft_of([record], [prepared(text)]).records[0]

    # The status is text of the source, so it is kept verbatim and not flagged.
    assert (stored.start_date, stored.end_date) == ("Accepted, to appear 2025", None)
    assert stored.needs_review is False
    assert [bullet.text for bullet in stored.bullets] == ["Role: First author"]
    assert stored.bullets[0].source_ref.excerpt == "Role: First author"


def test_publication_status_that_is_not_in_the_source_is_flagged() -> None:
    stored = draft_of(
        [paper(start_date="Best paper award 2023", summary=None)],
        [prepared(f"Publications\n{PAPER_HEADER}\n")],
    ).records[0]
    assert "The start date was not found in the source text." in stored.review_reasons


# ---- SPEC-03: contact details that differ between sources ---------------------------


def contact_sources(first: str, second: str) -> list:
    return [prepared(first), prepared(second, "S2", "LinkedIn")]


def test_different_email_addresses_across_sources_are_a_conflict() -> None:
    sources = contact_sources(
        "Riley Park\nriley.park@example.com\n", "Riley Park\nsomeone.else@example.org\n"
    )
    draft = draft_of(
        [],
        sources,
        contact=[
            contact_item("name", "Riley Park"),
            contact_item("email", "riley.park@example.com"),
            contact_item("name", "Riley Park", "S2"),
            contact_item("email", "someone.else@example.org", "S2"),
        ],
    )

    # The first source's value is shown, and the user has to decide.
    assert draft.contact.email == "riley.park@example.com"
    assert len(draft.conflicts) == 1
    conflict = draft.conflicts[0]
    assert (conflict.field, conflict.resolution) == ("contact_email", "unresolved")
    assert conflict.description == "The sources give different e-mail addresses."
    assert conflict.record_ids == []
    assert [(value.value, value.source_ref.source_label) for value in conflict.values] == [
        ("riley.park@example.com", "Resume"),
        ("someone.else@example.org", "LinkedIn"),
    ]
    assert conflict.values[1].source_ref.excerpt == "someone.else@example.org"


@pytest.mark.parametrize(
    ("field", "first", "second", "conflicting"),
    [
        ("email", "riley.park@example.com", "Riley.Park@Example.com", False),
        ("phone", "(614) 555-0142", "+1 614 555 0142", False),
        ("phone", "(614) 555-0142", "614-555-0199", True),
        ("name", "Riley Park", "Riley J. Park", False),
        ("name", "Riley Park", "Morgan Park", True),
    ],
)
def test_contact_values_are_compared_by_meaning(
    field: str, first: str, second: str, conflicting: bool
) -> None:
    draft = draft_of(
        [],
        contact_sources(f"{first}\n", f"{second}\n"),
        contact=[contact_item(field, first), contact_item(field, second, "S2")],
    )
    assert [conflict.field for conflict in draft.conflicts] == (
        [f"contact_{field}"] if conflicting else []
    )
    assert getattr(draft.contact, field) == first


def test_two_addresses_in_one_source_are_not_a_conflict() -> None:
    sources = contact_sources("work@example.com | home@example.com\n", "home@example.com\n")
    draft = draft_of(
        [],
        sources,
        contact=[
            contact_item("email", "work@example.com"),
            contact_item("email", "home@example.com"),
            contact_item("email", "home@example.com", "S2"),
        ],
    )
    assert draft.conflicts == []
    assert draft.contact.email == "work@example.com"


def test_contact_value_that_is_not_in_its_source_causes_no_conflict() -> None:
    sources = contact_sources("riley.park@example.com\n", "Riley Park\n")
    draft = draft_of(
        [],
        sources,
        contact=[
            contact_item("email", "riley.park@example.com"),
            contact_item("email", "invented@example.org", "S2"),
        ],
    )
    assert draft.conflicts == []


def test_contact_conflict_the_model_also_reported_is_listed_once() -> None:
    sources = contact_sources("riley.park@example.com\n", "someone.else@example.org\n")
    reported = LLMConflict(
        field="other",
        description="The two sources give different e-mail addresses.",
        record_indexes=[],
        values=[
            LLMConflictValue(
                value="riley.park@example.com", source="S1", quote="riley.park@example.com"
            ),
            LLMConflictValue(
                value="someone.else@example.org", source="S2", quote="someone.else@example.org"
            ),
        ],
    )
    draft = draft_of(
        [],
        sources,
        conflicts=[reported],
        contact=[
            contact_item("email", "riley.park@example.com"),
            contact_item("email", "someone.else@example.org", "S2"),
        ],
    )
    assert len(draft.conflicts) == 1
    assert draft.conflicts[0].field == "contact_email"
    assert draft.conflicts[0].description == "The two sources give different e-mail addresses."
    assert len(draft.conflicts[0].values) == 2


# ---- G-09: one date disagreement, listed once ---------------------------------------

OTHER_HEADER = "Data Engineer - Northwind Labs (Feb 2020 - Mar 2021)"
DEGREE_HEADER = "B.S. in Statistics - Lakeshore University (2015 - 2019)"


def date_conflict_draft(reported: LLMConflict) -> Draft:
    """The same role in two sources with different start months, plus a degree
    that has nothing to do with it, and one conflict reported by the model."""
    linkedin = f"Experience\n{OTHER_HEADER}\nEducation\n{DEGREE_HEADER}\n"
    sources = [prepared(RESUME), prepared(linkedin, "S2", "LinkedIn")]
    records = [
        llm_record(),
        llm_record(source="S2", start_date="Feb 2020", header_quote=OTHER_HEADER),
        llm_record(
            category="education",
            title="B.S. in Statistics",
            organization="Lakeshore University",
            start_date="2015",
            end_date="2019",
            source="S2",
            header_quote=DEGREE_HEADER,
        ),
    ]
    return draft_of(records, sources, conflicts=[reported])


def reported_conflict(
    field: str = "start_date",
    indexes: tuple[int, ...] = (0, 1),
    values: tuple[str, str] = ("Jan 2020", "Feb 2020"),
    quotes: tuple[str, str] = (HEADER, OTHER_HEADER),
) -> LLMConflict:
    return LLMConflict(
        field=field,
        description="The sources give different start dates for the Data Engineer role.",
        record_indexes=list(indexes),
        values=[
            LLMConflictValue(value=value, source=alias, quote=quote)
            for value, alias, quote in zip(values, ("S1", "S2"), quotes, strict=True)
        ],
    )


@pytest.mark.parametrize(
    "reported",
    [
        reported_conflict(),
        reported_conflict(field="other"),
        reported_conflict(field="dates"),
        reported_conflict(field="Start Date"),
        reported_conflict(field="employment.start_date"),
        reported_conflict(field="other", indexes=(0, 2)),
        reported_conflict(field="dates", quotes=("Jan 2020", "Feb 2020")),
        reported_conflict(
            field="dates", values=("Jan 2020 - Mar 2021", "Feb 2020 - Mar 2021"), indexes=(0, 2)
        ),
        reported_conflict(field="other", values=("January 2020", "February 2020")),
        reported_conflict(field="other", indexes=(), quotes=("not a quote", "also not a quote")),
    ],
    ids=[
        "same label",
        "label other",
        "label dates",
        "label with a space",
        "dotted label",
        "one wrong position",
        "quotes are only the dates",
        "values are date ranges",
        "months written out",
        "no record can be traced",
    ],
)
def test_date_conflict_reported_by_the_model_is_listed_once(reported: LLMConflict) -> None:
    """Seen on the deployed service: the same start-date disagreement appeared
    twice, once as reported by the model and once from the server's check."""
    draft = date_conflict_draft(reported)
    role = draft.records[0]

    assert len(draft.conflicts) == 1
    conflict = draft.conflicts[0]
    # The model's sentence is kept; field and record come from the server's check.
    assert conflict.description == reported.description
    assert conflict.field == "start_date"
    assert conflict.record_ids == [role.record_id]
    # The values are the stored dates, each with the header line it was read from.
    assert [(value.value, value.source_ref.excerpt) for value in conflict.values] == [
        ("Jan 2020", HEADER),
        ("Feb 2020", OTHER_HEADER),
    ]


def test_a_different_disagreement_about_the_same_role_stays_a_second_entry() -> None:
    reported = reported_conflict(field="end_date", values=("Mar 2021", "Apr 2021"))
    draft = date_conflict_draft(reported)
    assert sorted(conflict.field for conflict in draft.conflicts) == ["end_date", "start_date"]
    start = next(conflict for conflict in draft.conflicts if conflict.field == "start_date")
    assert [value.value for value in start.values] == ["Jan 2020", "Feb 2020"]


def test_same_dates_of_another_record_are_not_merged_into_its_conflict() -> None:
    """A model conflict that is linked to a different record keeps its own entry."""
    reported = reported_conflict(field="other", indexes=(2,), quotes=("nowhere", "nowhere"))
    draft = date_conflict_draft(reported)
    assert len(draft.conflicts) == 2


def test_merged_role_from_linkedin_still_reports_no_uncaptured_header() -> None:
    """The header of a role merged into the resume's record was quoted too."""
    sources = [prepared(RESUME), prepared(LINKEDIN, "S2", "LinkedIn")]
    resume_role = llm_record(
        bullets=[
            llm_bullet("Built Python pipelines for 12 analysts"),
            llm_bullet("Cut report time by 40%"),
        ]
    )
    linkedin_role = llm_record(
        source="S2",
        bullets=[
            llm_bullet("Built Python pipelines for 12 analysts", "S2"),
            llm_bullet("Mentored one intern", "S2"),
        ],
    )
    skills = llm_record(
        category="skill",
        title="Skills",
        organization=None,
        start_date=None,
        end_date=None,
        header_quote="Python, JavaScript, C",
        skills=["Python", "JavaScript", "C"],
    )
    assert uncaptured(draft_of([resume_role, linkedin_role, skills], sources)) == []


def test_third_source_with_another_date_joins_the_same_conflict_in_source_order() -> None:
    third_header = "Data Engineer - Northwind Labs (Mar 2020 - Mar 2021)"
    sources = [
        prepared(RESUME),
        prepared(f"Experience\n{OTHER_HEADER}\n", "S2", "LinkedIn"),
        prepared(f"Experience\n{third_header}\n", "S3", "Notes"),
    ]
    records = [
        llm_record(),
        llm_record(source="S2", start_date="Feb 2020", header_quote=OTHER_HEADER),
        llm_record(source="S3", start_date="Mar 2020", header_quote=third_header),
    ]
    draft = draft_of(records, sources)
    assert len(draft.records) == 1
    assert len(draft.conflicts) == 1
    assert [value.value for value in draft.conflicts[0].values] == [
        "Jan 2020",
        "Feb 2020",
        "Mar 2020",
    ]

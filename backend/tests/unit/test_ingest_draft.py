"""Checking a model extraction against the sources (ingestion.build_draft):
source references, review flags, merging of duplicates and conflicts.

The extractions here are written by hand, so the tests also cover output that
a real model could produce but the fake provider never does (wrong quotes,
invented skills, paraphrased text)."""

import json
from pathlib import Path
from typing import Any

from app.providers.base import (
    LLMBullet,
    LLMConflict,
    LLMConflictValue,
    LLMContactItem,
    LLMExtraction,
    LLMRecord,
    LLMSource,
)
from app.providers.fake.extraction_ops import extract_profile
from app.schemas.profiles import ProfilePatchRequest
from app.services.ingestion import (
    AMBIGUOUS,
    MAX_BULLET_CHARS,
    MAX_TITLE_CHARS,
    NO_DATES,
    SPAN_NOT_FOUND,
    Draft,
    PreparedSource,
    build_draft,
    prepare_source,
)
from app.services.textutil import fold_text, normalize_whitespace
from tests.factories import make_source

PROFILES = Path(__file__).resolve().parents[2] / "fixtures" / "profiles"

RESUME = (
    "Jordan Rivera\n"
    "Experience\n"
    "Data Engineer - Northwind Labs (Jan 2020 - Mar 2021)\n"
    "- Built Python pipelines for 12 analysts\n"
    "- Cut report time by 40%\n"
    "Skills\n"
    "Python, JavaScript, C\n"
)
LINKEDIN = (
    "Experience\n"
    "Data Engineer - Northwind Labs (Jan 2020 - Mar 2021)\n"
    "- Built Python pipelines for 12 analysts\n"
    "- Mentored one intern\n"
)
HEADER = "Data Engineer - Northwind Labs (Jan 2020 - Mar 2021)"


def prepared(text: str, alias: str = "S1", label: str = "Resume") -> PreparedSource:
    return prepare_source(alias, make_source("owner-1", text=text, label=label))


def llm_bullet(quote: str, source: str = "S1") -> LLMBullet:
    return LLMBullet(source=source, quote=quote)


def llm_record(**overrides: Any) -> LLMRecord:
    values: dict[str, Any] = {
        "category": "employment",
        "title": "Data Engineer",
        "organization": "Northwind Labs",
        "location": None,
        "start_date": "Jan 2020",
        "end_date": "Mar 2021",
        "summary": None,
        "source": "S1",
        "header_quote": HEADER,
        "bullets": [],
        "skills": [],
        "ambiguous": False,
        "ambiguity_notes": [],
    }
    values.update(overrides)
    return LLMRecord(**values)


def draft_of(
    records: list[LLMRecord],
    sources: list[PreparedSource] | None = None,
    conflicts: list[LLMConflict] | None = None,
    contact: list[LLMContactItem] | None = None,
) -> Draft:
    extraction = LLMExtraction(contact=contact or [], records=records, conflicts=conflicts or [])
    return build_draft(extraction, sources or [prepared(RESUME)])


# ---- Source references -------------------------------------------------------------


def test_quotes_are_mapped_to_offsets_in_the_original_text() -> None:
    original = (
        "Jordan  Rivera\r\n\r\n\r\nExperience\r\n"
        "  Data   Engineer -  Northwind Labs (Jan 2020 -\tMar 2021)\r\n"
        "\t•  Built   Python pipelines for 12 analysts\r\n"
    )
    source = prepared(original)
    # The model reads the normalised copy and quotes from it.
    assert HEADER in source.normalized.text
    record = llm_record(bullets=[llm_bullet("Built Python pipelines for 12 analysts")])

    stored = draft_of([record], [source]).records[0]

    header_ref = stored.source_ref
    assert header_ref is not None
    assert header_ref.excerpt == original[header_ref.start : header_ref.end]
    assert header_ref.excerpt == "Data   Engineer -  Northwind Labs (Jan 2020 -\tMar 2021)"
    bullet_ref = stored.bullets[0].source_ref
    assert bullet_ref is not None
    assert bullet_ref.excerpt == original[bullet_ref.start : bullet_ref.end]
    assert bullet_ref.excerpt == "Built   Python pipelines for 12 analysts"
    assert bullet_ref.source_id == source.doc.source_id
    assert bullet_ref.source_label == "Resume"
    assert stored.needs_review is False
    assert stored.bullets[0].needs_review is False
    assert stored.bullets[0].provenance == "extracted"


def test_quote_style_differences_are_tolerated() -> None:
    original = "Experience\nEngineer - Acme (2020 - 2021)\n- Led the team’s “platform” rewrite\n"
    record = llm_record(
        title="Engineer",
        organization="Acme",
        start_date="2020",
        end_date="2021",
        header_quote="Engineer - Acme (2020 - 2021)",
        bullets=[llm_bullet('Led the team\'s "platform" rewrite')],
    )
    bullet = draft_of([record], [prepared(original)]).records[0].bullets[0]
    assert bullet.needs_review is False
    assert bullet.source_ref.excerpt == "Led the team’s “platform” rewrite"


def test_a_repeated_sentence_is_located_under_its_own_role() -> None:
    original = (
        "Experience\n"
        "Engineer - Acme (2019 - 2020)\n- Wrote unit tests\n"
        "Engineer - Bolt (2020 - 2021)\n- Wrote unit tests\n"
    )
    second = llm_record(
        title="Engineer",
        organization="Bolt",
        start_date="2020",
        end_date="2021",
        header_quote="Engineer - Bolt (2020 - 2021)",
        bullets=[llm_bullet("Wrote unit tests")],
    )
    stored = draft_of([second], [prepared(original)]).records[0]
    assert stored.bullets[0].source_ref.start > stored.source_ref.start


def test_quote_that_is_not_in_the_source_flags_the_item_for_review() -> None:
    record = llm_record(
        header_quote="Senior Data Engineer at Northwind (2020-2021)",
        bullets=[
            llm_bullet("Built Python pipelines for 12 analysts"),
            llm_bullet("Managed a team of 30"),
        ],
    )
    stored = draft_of([record]).records[0]

    assert stored.source_ref is None
    assert stored.needs_review is True
    assert SPAN_NOT_FOUND in stored.review_reasons
    located, invented = stored.bullets
    assert located.needs_review is False
    assert invented.source_ref is None
    assert invented.needs_review is True
    assert invented.review_reasons == [SPAN_NOT_FOUND]


def test_unknown_source_alias_is_treated_as_a_missing_span() -> None:
    record = llm_record(source="S9", bullets=[llm_bullet("Cut report time by 40%", source="S9")])
    stored = draft_of([record]).records[0]
    assert stored.source_ref is None
    assert SPAN_NOT_FOUND in stored.review_reasons
    assert stored.bullets[0].review_reasons == [SPAN_NOT_FOUND]


def test_reworded_statement_is_not_accepted_as_a_quote() -> None:
    record = llm_record(bullets=[llm_bullet("Cut report time by 60%")])
    bullet = draft_of([record]).records[0].bullets[0]
    # Shown to the user as proposed, but without a source and flagged.
    assert bullet.text == "Cut report time by 60%"
    assert bullet.source_ref is None
    assert bullet.review_reasons == [SPAN_NOT_FOUND]


def test_statement_text_is_the_quote_on_one_line_without_its_list_marker() -> None:
    original = (
        "Experience\n"
        "Data Engineer - Northwind Labs (Jan 2020 - Mar 2021)\n"
        "  \u2022 Built Python pipelines\n"
        "    for 12 analysts\n"
    )
    source = prepared(original)
    # A wrapped bullet keeps its line break in the copy the model reads.
    quote = "\u2022 Built Python pipelines\nfor 12 analysts"
    assert quote in source.normalized.text

    bullet = draft_of([llm_record(bullets=[llm_bullet(quote)])], [source]).records[0].bullets[0]

    assert bullet.text == "Built Python pipelines for 12 analysts"
    assert bullet.needs_review is False
    assert bullet.source_ref.excerpt == "\u2022 Built Python pipelines\n    for 12 analysts"
    assert original[bullet.source_ref.start : bullet.source_ref.end] == bullet.source_ref.excerpt


def test_fact_that_is_not_written_in_the_source_is_flagged() -> None:
    stored = draft_of([llm_record(start_date="Jan 2019", organization="Northwind Global")])
    record = stored.records[0]
    assert record.needs_review is True
    assert "The start date was not found in the source text." in record.review_reasons
    assert "The organization was not found in the source text." in record.review_reasons
    # The value is shown for review, not silently replaced.
    assert record.start_date == "Jan 2019"


def test_skills_that_are_not_in_any_source_are_left_out() -> None:
    record = llm_record(
        category="skill",
        title="Skills",
        organization=None,
        start_date=None,
        end_date=None,
        header_quote="Python, JavaScript, C",
        skills=["Python", "Java", "Kubernetes", "C", "python"],
    )
    stored = draft_of([record]).records[0]
    # "Java" only occurs inside "JavaScript"; "C" is a whole term of the list.
    assert stored.skills == ["Python", "C"]
    assert stored.needs_review is True
    assert any('"Kubernetes"' in reason for reason in stored.review_reasons)
    assert any('"Java"' in reason for reason in stored.review_reasons)


def test_label_of_an_unlabelled_skill_list_needs_no_source() -> None:
    text = "Technologies\nPython, SQL\n"
    record = llm_record(
        category="skill",
        title="Skills",
        organization=None,
        start_date=None,
        end_date=None,
        header_quote="Python, SQL",
        skills=["Python", "SQL"],
    )
    stored = draft_of([record], [prepared(text)]).records[0]
    assert (stored.title, stored.skills, stored.needs_review) == (
        "Skills",
        ["Python", "SQL"],
        False,
    )


def test_model_flagged_ambiguity_becomes_review_reasons() -> None:
    noted = llm_record(ambiguous=True, ambiguity_notes=["Unclear whether this was full time."])
    unexplained = llm_record(organization="Northwind", ambiguous=True)
    first, second = draft_of([noted, unexplained]).records
    assert first.review_reasons == ["Unclear whether this was full time."]
    assert first.needs_review is True
    assert second.review_reasons == [AMBIGUOUS]


def test_role_or_degree_without_dates_is_flagged_but_a_project_is_not() -> None:
    text = "Experience\nVolunteer - Food Bank\nProjects\nBird Feeder Camera - Hobby project\n"
    role = llm_record(
        title="Volunteer",
        organization="Food Bank",
        start_date=None,
        end_date=None,
        header_quote="Volunteer - Food Bank",
    )
    project = llm_record(
        category="project",
        title="Bird Feeder Camera",
        organization="Hobby project",
        start_date=None,
        end_date=None,
        header_quote="Bird Feeder Camera - Hobby project",
    )
    stored_role, stored_project = draft_of([role, project], [prepared(text)]).records
    assert stored_role.review_reasons == [NO_DATES]
    assert (stored_role.start_date, stored_role.end_date) == (None, None)
    assert stored_project.needs_review is False


def test_record_without_a_title_is_dropped() -> None:
    assert draft_of([llm_record(title="   ")]).records == []


# ---- Duplicates --------------------------------------------------------------------


def test_exact_duplicate_bullets_inside_a_record_are_removed() -> None:
    record = llm_record(
        bullets=[
            llm_bullet("Cut report time by 40%"),
            llm_bullet("- cut  report time by 40%"),
            llm_bullet("Built Python pipelines for 12 analysts"),
        ]
    )
    bullets = draft_of([record]).records[0].bullets
    assert [bullet.text for bullet in bullets] == [
        "Cut report time by 40%",
        "Built Python pipelines for 12 analysts",
    ]


def test_same_role_in_two_sources_becomes_one_record() -> None:
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
    draft = draft_of([resume_role, linkedin_role], sources)

    assert len(draft.records) == 1
    assert draft.conflicts == []
    record = draft.records[0]
    assert record.source_ref.source_label == "Resume"
    assert [(bullet.text, bullet.source_ref.source_label) for bullet in record.bullets] == [
        ("Built Python pipelines for 12 analysts", "Resume"),
        ("Cut report time by 40%", "Resume"),
        ("Mentored one intern", "LinkedIn"),
    ]


def test_distinct_roles_are_never_merged() -> None:
    text = (
        "Experience\n"
        "Data Engineer - Northwind Labs (Jan 2020 - Mar 2021)\n"
        "Senior Data Engineer - Northwind Labs (Apr 2021 - Present)\n"
        "Data Engineer - Globex (Jan 2018 - Dec 2019)\n"
    )
    promoted = llm_record(
        title="Senior Data Engineer",
        start_date="Apr 2021",
        end_date="Present",
        header_quote="Senior Data Engineer - Northwind Labs (Apr 2021 - Present)",
    )
    elsewhere = llm_record(
        organization="Globex",
        start_date="Jan 2018",
        end_date="Dec 2019",
        header_quote="Data Engineer - Globex (Jan 2018 - Dec 2019)",
    )
    draft = draft_of([llm_record(), promoted, elsewhere], [prepared(text)])
    assert [(record.title, record.organization) for record in draft.records] == [
        ("Data Engineer", "Northwind Labs"),
        ("Senior Data Engineer", "Northwind Labs"),
        ("Data Engineer", "Globex"),
    ]
    assert draft.conflicts == []


def test_two_periods_in_the_same_job_within_one_source_stay_separate() -> None:
    text = (
        "Experience\n"
        "Barista - Corner Cafe (Jun 2019 - Aug 2019)\n"
        "Barista - Corner Cafe (Jun 2020 - Aug 2020)\n"
    )
    summers = [
        llm_record(
            title="Barista",
            organization="Corner Cafe",
            start_date=f"Jun {year}",
            end_date=f"Aug {year}",
            header_quote=f"Barista - Corner Cafe (Jun {year} - Aug {year})",
        )
        for year in (2019, 2020)
    ]
    draft = draft_of(summers, [prepared(text)])
    assert [record.start_date for record in draft.records] == ["Jun 2019", "Jun 2020"]
    assert draft.conflicts == []


def test_second_description_of_a_merged_role_is_kept_as_a_statement() -> None:
    linkedin = f"Experience\n{HEADER}\nOwned the nightly data platform.\n"
    sources = [prepared(RESUME), prepared(linkedin, "S2", "LinkedIn")]
    described = llm_record(source="S2", summary="Owned the nightly data platform.")
    record = draft_of([llm_record(), described], sources).records[0]
    assert record.summary is None
    assert [(b.text, b.source_ref.source_label) for b in record.bullets] == [
        ("Owned the nightly data platform.", "LinkedIn")
    ]


def test_skills_named_in_an_earlier_group_are_not_repeated_in_a_later_one() -> None:
    linkedin = "Skills\nPython, SQL, python\n"
    sources = [prepared(RESUME), prepared(linkedin, "S2", "LinkedIn")]
    blank = {"organization": None, "start_date": None, "end_date": None, "category": "skill"}
    groups = [
        llm_record(
            **blank, title="Languages", header_quote="Python, JavaScript, C", skills=["Python"]
        ),
        llm_record(
            **blank,
            title="Skills",
            source="S2",
            header_quote="Python, SQL, python",
            skills=["Python", "SQL", "python"],
        ),
        llm_record(
            **blank, title="More", source="S2", header_quote="Python, SQL, python", skills=["SQL"]
        ),
    ]
    draft = draft_of(groups, sources)
    # The third group has nothing new to add, so it is dropped.
    assert [(record.title, record.skills) for record in draft.records] == [
        ("Languages", ["Python"]),
        ("Skills", ["SQL"]),
    ]


# ---- Conflicts ---------------------------------------------------------------------


def test_different_dates_across_sources_merge_the_role_and_report_a_conflict() -> None:
    other_header = "Data Engineer - Northwind Labs (Feb 2020 - Mar 2021)"
    linkedin = f"Experience\n{other_header}\n- Mentored one intern\n"
    sources = [prepared(RESUME), prepared(linkedin, "S2", "LinkedIn")]
    disagreeing = llm_record(
        source="S2",
        start_date="Feb 2020",
        header_quote=other_header,
        bullets=[llm_bullet("Mentored one intern", "S2")],
    )
    draft = draft_of([llm_record(), disagreeing], sources)

    # One role, not two, and no date is picked silently: the first source's
    # date is shown and the disagreement has to be resolved by the user.
    assert len(draft.records) == 1
    record = draft.records[0]
    assert record.start_date == "Jan 2020"
    assert [bullet.text for bullet in record.bullets] == ["Mentored one intern"]
    assert len(draft.conflicts) == 1
    conflict = draft.conflicts[0]
    assert conflict.field == "start_date"
    assert conflict.resolution == "unresolved"
    assert conflict.record_ids == [record.record_id]
    assert [(value.value, value.source_ref.source_label) for value in conflict.values] == [
        ("Jan 2020", "Resume"),
        ("Feb 2020", "LinkedIn"),
    ]
    assert conflict.values[1].source_ref.excerpt == other_header
    assert "Data Engineer at Northwind Labs" in conflict.description


def test_differently_written_equal_dates_are_not_a_conflict() -> None:
    linkedin = "Experience\nData Engineer - Northwind Labs (January 2020 - Mar. 2021)\n"
    sources = [prepared(RESUME), prepared(linkedin, "S2", "LinkedIn")]
    same_dates = llm_record(
        source="S2",
        start_date="January 2020",
        end_date="Mar. 2021",
        header_quote="Data Engineer - Northwind Labs (January 2020 - Mar. 2021)",
    )
    draft = draft_of([llm_record(), same_dates], sources)
    assert len(draft.records) == 1
    assert draft.conflicts == []
    # The stored date stays exactly as the first source wrote it.
    assert draft.records[0].start_date == "Jan 2020"


def test_present_and_current_mean_the_same_end_date() -> None:
    resume = "Experience\nEngineer - Acme (2021 - Present)\n"
    linkedin = "Experience\nEngineer - Acme (2021 - Current)\n"
    records = [
        llm_record(
            title="Engineer",
            organization="Acme",
            start_date="2021",
            end_date=end,
            source=alias,
            header_quote=f"Engineer - Acme (2021 - {end})",
        )
        for alias, end in (("S1", "Present"), ("S2", "Current"))
    ]
    draft = draft_of(records, [prepared(resume), prepared(linkedin, "S2", "LinkedIn")])
    assert len(draft.records) == 1
    assert draft.conflicts == []


def test_model_reported_conflict_is_not_duplicated_by_the_date_check() -> None:
    other_header = "Data Engineer - Northwind Labs (Feb 2020 - Mar 2021)"
    sources = [prepared(RESUME), prepared(f"Experience\n{other_header}\n", "S2", "LinkedIn")]
    reported = LLMConflict(
        field="start_date",
        description="The start month differs between the resume and LinkedIn.",
        record_indexes=[0, 1, 7],
        values=[
            LLMConflictValue(value="Jan 2020", source="S1", quote=HEADER),
            LLMConflictValue(value="Feb 2020", source="S2", quote=other_header),
        ],
    )
    records = [
        llm_record(),
        llm_record(source="S2", start_date="Feb 2020", header_quote=other_header),
    ]
    draft = draft_of(records, sources, conflicts=[reported])

    assert len(draft.conflicts) == 1
    conflict = draft.conflicts[0]
    assert conflict.description == "The start month differs between the resume and LinkedIn."
    # Both of the model's records ended up in one profile record; index 7 does not exist.
    assert conflict.record_ids == [draft.records[0].record_id]
    assert [value.value for value in conflict.values] == ["Jan 2020", "Feb 2020"]
    assert conflict.values[1].source_ref.excerpt == other_header


def test_model_reported_conflict_about_another_field_is_kept() -> None:
    text = (
        "Experience\n"
        "Data Engineer - Northwind Labs (Jan 2020 - Mar 2021)\n"
        "Analytics Engineer - Northwind Labs (Jan 2020 - Mar 2021)\n"
    )
    other = llm_record(
        title="Analytics Engineer",
        header_quote="Analytics Engineer - Northwind Labs (Jan 2020 - Mar 2021)",
    )
    reported = LLMConflict(
        field="Title",
        description="Two different titles are given for the same period.",
        record_indexes=[0, 1],
        values=[
            LLMConflictValue(value="Data Engineer", source="S1", quote="Data Engineer"),
            LLMConflictValue(value="Analytics Engineer", source="S1", quote="not in the text"),
        ],
    )
    draft = draft_of([llm_record(), other], [prepared(text)], conflicts=[reported])

    assert len(draft.records) == 2
    conflict = draft.conflicts[0]
    assert conflict.field == "title"
    assert conflict.record_ids == [record.record_id for record in draft.records]
    assert conflict.values[0].source_ref is not None
    assert conflict.values[1].source_ref is None


# ---- Contact -----------------------------------------------------------------------


def test_contact_uses_the_first_value_that_is_written_in_its_source() -> None:
    resume = "Jordan Rivera\njordan@example.com | https://example.com/jr\nExperience\n"
    sources = [prepared(resume), prepared("Jordan A. Rivera\nBoston, MA\n", "S2", "LinkedIn")]

    def item(field: str, value: str, source: str = "S1") -> LLMContactItem:
        return LLMContactItem(field=field, value=value, source=source, quote=value)

    contact = draft_of(
        [],
        sources,
        contact=[
            item("name", "Jordan Rivera"),
            item("email", "someone.else@example.com"),
            item("email", "jordan@example.com"),
            item("phone", "+1 555 0100"),
            item("link", "https://example.com/jr"),
            item("link", "https://example.com/jr"),
            item("name", "Jordan A. Rivera", "S2"),
            item("location", "Boston, MA", "S2"),
            item("location", "Paris, France", "S9"),
        ],
    ).contact

    assert contact.name == "Jordan Rivera"
    assert contact.email == "jordan@example.com"
    assert contact.phone is None
    assert contact.location == "Boston, MA"
    assert contact.links == ["https://example.com/jr"]


# ---- Whole pipeline on the fictional sample ----------------------------------------


def sample_draft() -> tuple[Draft, dict[str, str]]:
    files = [("Resume", "sample_resume.txt"), ("LinkedIn", "sample_linkedin.txt")]
    files.append(("Notes", "sample_notes.txt"))
    texts = {label: (PROFILES / name).read_text(encoding="utf-8") for label, name in files}
    sources = [
        prepared(text, f"S{number}", label) for number, (label, text) in enumerate(texts.items(), 1)
    ]
    extraction = extract_profile(
        [
            LLMSource(
                alias=s.alias, label=s.doc.label, source_type="resume", text=s.normalized.text
            )
            for s in sources
        ]
    )
    return build_draft(extraction, sources), texts


def test_sample_profile_draft_matches_the_expected_facts() -> None:
    expected = json.loads((PROFILES / "sample_expected.json").read_text(encoding="utf-8"))
    draft, texts = sample_draft()

    roles = [record for record in draft.records if record.category == "employment"]
    dated = [role for role in roles if role.start_date]
    assert len(dated) == expected["counts"]["dated_roles"]
    assert len(roles) - len(dated) == expected["counts"]["undated_roles"]
    by_category = [record.category for record in draft.records]
    assert by_category.count("project") == expected["counts"]["projects"]
    assert by_category.count("education") == expected["counts"]["degrees"]
    assert by_category.count("certification") == expected["counts"]["certifications"]

    for role, facts in zip(dated, expected["roles"], strict=True):
        assert (role.title, role.organization) == (facts["title"], facts["employer"])
        assert f"{role.start_date} - {role.end_date}" == facts["date_string"]

    # Every reference points at exactly the text it quotes.
    for record in draft.records:
        for ref in [record.source_ref, *(bullet.source_ref for bullet in record.bullets)]:
            assert ref is not None
            assert texts[ref.source_label][ref.start : ref.end] == ref.excerpt

    statements = [bullet.text for record in draft.records for bullet in record.bullets]
    titles = [record.title for record in draft.records]
    for number in expected["key_numbers"]:
        assert any(number["text"] in text for text in statements + titles), number["text"]
    assert statements.count(expected["duplicated_bullet"]["text"]) == 1

    undated = next(role for role in roles if not role.start_date)
    assert undated.title == expected["ambiguous_items"][0]["title"]
    assert undated.needs_review is True
    flagged = [record for record in draft.records if record.needs_review]
    assert flagged == [undated]

    conflict = draft.conflicts[0]
    assert len(draft.conflicts) == 1
    assert {value.value for value in conflict.values} == set(
        expected["expected_conflict"]["values"].values()
    )
    quillfeather = dated[1]
    assert conflict.record_ids == [quillfeather.record_id]
    assert quillfeather.start_date == expected["expected_conflict"]["values"]["resume"]

    skills = [skill for record in draft.records for skill in record.skills]
    assert len(skills) == len({skill.casefold() for skill in skills})
    link = expected["contact"]["link"]
    assert draft.contact.links == [link]
    assert draft.contact.model_dump(exclude={"links"}) == {
        field: value for field, value in expected["contact"].items() if field != "link"
    }


def test_oversized_extraction_still_gives_a_profile_that_can_be_saved() -> None:
    long_title = "Data Engineer " + "x" * 400
    long_statement = "Built Python pipelines " + "y" * 5000
    text = f"Experience\n{long_title} - Northwind Labs (Jan 2020 - Mar 2021)\n- {long_statement}\n"
    record = llm_record(
        title=long_title,
        header_quote=f"{long_title} - Northwind Labs (Jan 2020 - Mar 2021)",
        bullets=[llm_bullet(long_statement)] + [llm_bullet(f"Item {n}") for n in range(120)],
    )
    stored = draft_of([record], [prepared(text)]).records[0]

    assert len(stored.title) == MAX_TITLE_CHARS
    assert len(stored.bullets[0].text) <= MAX_BULLET_CHARS
    assert stored.needs_review is True
    # The draft can be sent back unchanged through PATCH /api/profile.
    ProfilePatchRequest.model_validate(
        {
            "expected_version": 1,
            "contact": {},
            "records": [
                {
                    **stored.model_dump(include={"record_id", "category", "title", "skills"}),
                    "bullets": [
                        {"bullet_id": bullet.bullet_id, "text": bullet.text}
                        for bullet in stored.bullets
                    ],
                }
            ],
        }
    )


def test_fold_text_ignores_case_and_spacing_only() -> None:
    assert fold_text("  Built\tPython\n pipelines ") == "built python pipelines"
    assert fold_text("STRASSE") == fold_text("stra\u00dfe")
    assert fold_text("C++") != fold_text("C")
    assert fold_text("") == ""


def test_normalised_copy_is_what_the_model_reads() -> None:
    source = prepared("Line  one\r\n\r\n\r\n\tLine two  ")
    assert (
        source.normalized.text
        == normalize_whitespace(source.doc.text).text
        == "Line one\n\nLine two"
    )
    assert source.doc.text == "Line  one\r\n\r\n\r\n\tLine two  "

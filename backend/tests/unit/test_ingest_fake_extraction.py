"""The fake provider's rule-based profile extraction: common resume layouts,
verbatim quotes, cross-source conflicts and instruction-like text."""

import json
from pathlib import Path

from app.providers.base import LLMExtraction, LLMRecord, LLMSource
from app.providers.fake.extraction_ops import extract_profile
from app.services.textutil import normalize_whitespace

PROFILES = Path(__file__).resolve().parents[2] / "fixtures" / "profiles"


def source(text: str, alias: str = "S1", source_type: str = "resume") -> LLMSource:
    """A source as the ingestion service hands it to the provider."""
    return LLMSource(
        alias=alias, label=alias, source_type=source_type, text=normalize_whitespace(text).text
    )


def sample_sources() -> list[LLMSource]:
    names = [("sample_resume.txt", "resume"), ("sample_linkedin.txt", "linkedin")]
    names.append(("sample_notes.txt", "notes"))
    return [
        source((PROFILES / name).read_text(encoding="utf-8"), f"S{number}", kind)
        for number, (name, kind) in enumerate(names, start=1)
    ]


def records_of(extraction: LLMExtraction, category: str, alias: str = "S1") -> list[LLMRecord]:
    return [r for r in extraction.records if r.category == category and r.source == alias]


def header(record: LLMRecord) -> tuple:
    return record.title, record.organization, record.start_date, record.end_date


# ---- The fictional sample profile --------------------------------------------------


def test_sample_resume_roles_projects_education_and_certification_are_extracted() -> None:
    extraction = extract_profile(sample_sources())
    expected = json.loads((PROFILES / "sample_expected.json").read_text(encoding="utf-8"))

    roles = [header(record) for record in records_of(extraction, "employment")]
    assert roles == [
        (role["title"], role["employer"], role["start"], role["end"]) for role in expected["roles"]
    ]
    assert [header(r)[:2] for r in records_of(extraction, "project")] == [
        (project["name"], project["context"]) for project in expected["projects"]
    ]
    assert [header(r) for r in records_of(extraction, "education")] == [
        ("B.S. in Computer Science", "Fairhaven Institute of Technology", "Aug 2018", "May 2022")
    ]
    # A single date is not a range: it is kept as written in start_date.
    assert [header(r) for r in records_of(extraction, "certification")] == [
        ("AWS Certified Cloud Practitioner", "Amazon Web Services", "Mar 2024", None)
    ]


def test_every_quote_is_verbatim_text_of_its_source() -> None:
    sources = sample_sources()
    texts = {item.alias: item.text for item in sources}
    extraction = extract_profile(sources)

    for record in extraction.records:
        assert record.header_quote in texts[record.source]
        for bullet in record.bullets:
            assert bullet.quote in texts[bullet.source]
            assert not bullet.quote.startswith("- ")
    for item in extraction.contact:
        assert item.quote in texts[item.source]


def test_bullets_stay_with_their_own_role() -> None:
    extraction = extract_profile(sample_sources())
    intern = records_of(extraction, "employment")[2]
    assert [bullet.quote for bullet in intern.bullets] == [
        "Wrote Python scripts that parse robot telemetry logs and flag sensor dropouts, "
        "processing 1.2 million log lines per day",
        "Added a Flask REST API endpoint and a small web page for viewing flagged dropouts",
    ]


def test_contact_details_come_from_the_lines_above_the_first_heading() -> None:
    extraction = extract_profile(sample_sources())
    from_resume = {(item.field, item.value) for item in extraction.contact if item.source == "S1"}
    assert from_resume == {
        ("name", "Jordan Rivera"),
        ("email", "jordan.rivera@example.com"),
        ("phone", "(614) 555-0142"),
        ("location", "Columbus, OH"),
        ("link", "https://example.com/in/jordan-rivera"),
    }
    # The notes start with a sentence, not a name.
    assert not [item for item in extraction.contact if item.source == "S3"]


def test_skill_lines_become_groups_and_brackets_keep_a_skill_together() -> None:
    extraction = extract_profile(sample_sources())
    groups = {record.title: record.skills for record in records_of(extraction, "skill")}
    assert groups["Languages"] == ["Python", "TypeScript", "JavaScript", "SQL"]
    assert "AWS (S3, EC2, Lambda)" in groups["Tools"]

    notes = {record.title: record.skills for record in records_of(extraction, "skill", "S3")}
    # The qualifier stays attached, so limited exposure is never shown as a plain skill.
    assert notes["Coursework exposure only"] == [
        "TensorFlow (one assignment in a deep learning course)"
    ]
    # A list without a "Category:" prefix is named after its section heading.
    assert records_of(extraction, "skill", "S2")[0].title == "Skills"


def test_summary_and_about_paragraphs_are_not_records() -> None:
    extraction = extract_profile(sample_sources())
    everything = extraction.model_dump_json()
    assert "four years of software experience" not in everything
    assert "I build machine learning features" not in everything


def test_an_undated_role_gets_no_invented_dates() -> None:
    extraction = extract_profile(sample_sources())
    volunteer = records_of(extraction, "employment", "S3")[0]
    assert header(volunteer) == (
        "Volunteer Web Developer",
        "Cedar Hollow Community Library",
        None,
        None,
    )
    assert len(volunteer.bullets) == 2


def test_list_item_without_a_header_becomes_its_own_record() -> None:
    extraction = extract_profile(sample_sources())
    achievement = records_of(extraction, "achievement", "S3")[0]
    assert achievement.title.startswith("Won second place out of 18 teams")
    assert achievement.header_quote == achievement.title


def test_different_dates_for_the_same_role_in_two_sources_are_reported() -> None:
    extraction = extract_profile(sample_sources())

    assert len(extraction.conflicts) == 1
    conflict = extraction.conflicts[0]
    assert conflict.field == "start_date"
    assert [(value.value, value.source) for value in conflict.values] == [
        ("Jul 2022", "S1"),
        ("Jun 2022", "S2"),
    ]
    assert conflict.values[1].quote == (
        "Software Engineer - Quillfeather Software (Jun 2022 - Jul 2024)"
    )
    disagreeing = [extraction.records[index] for index in conflict.record_indexes]
    assert {record.organization for record in disagreeing} == {"Quillfeather Software"}
    # The role is reported once per source; nothing was merged or picked here.
    assert {record.source for record in disagreeing} == {"S1", "S2"}


def test_two_periods_in_the_same_job_within_one_source_are_not_a_conflict() -> None:
    text = (
        "Experience\n"
        "Barista - Corner Cafe (Jun 2019 - Aug 2019)\n- Served customers\n"
        "Barista - Corner Cafe (Jun 2020 - Aug 2020)\n- Trained two new hires\n"
    )
    extraction = extract_profile([source(text)])
    assert len(extraction.records) == 2
    assert extraction.conflicts == []


# ---- Other common layouts ----------------------------------------------------------


def test_pipe_separated_header_with_location_and_bullet_glyphs() -> None:
    text = (
        "Work Experience\n"
        "Data Analyst | Northwind Traders | Leeds, UK | March 2019 – June 2021\n"
        "• Built weekly sales reports\n"
        "* Automated data checks\n"
    )
    record = extract_profile([source(text)]).records[0]
    assert header(record) == ("Data Analyst", "Northwind Traders", "March 2019", "June 2021")
    assert record.location == "Leeds, UK"
    assert [bullet.quote for bullet in record.bullets] == [
        "Built weekly sales reports",
        "Automated data checks",
    ]


def test_comma_header_followed_by_a_date_line_and_a_description() -> None:
    text = (
        "EXPERIENCE\n"
        "Research Assistant, Lakeside University\n"
        "Sep 2020 - Present\n"
        "Supports a soil chemistry lab.\n"
        "- Prepared 200 samples per month\n"
    )
    record = extract_profile([source(text)]).records[0]
    assert header(record) == ("Research Assistant", "Lakeside University", "Sep 2020", "Present")
    assert record.header_quote == "Research Assistant, Lakeside University"
    assert record.summary == "Supports a soil chemistry lab."
    assert [bullet.quote for bullet in record.bullets] == ["Prepared 200 samples per month"]


def test_a_sentence_with_commas_is_not_mistaken_for_a_header() -> None:
    text = (
        "Projects\n"
        "Recipe Box - Course project (2021)\n"
        "- Built with React, Flask, and PostgreSQL\n"
        "Some closing remark, written as plain text\n"
    )
    extraction = extract_profile([source(text)])
    assert [record.title for record in extraction.records] == ["Recipe Box"]


def test_role_without_an_organisation_is_flagged_as_ambiguous() -> None:
    text = "Experience\nFreelance Designer (2018 - 2020)\n- Designed logos\n"
    record = extract_profile([source(text)]).records[0]
    assert header(record) == ("Freelance Designer", None, "2018", "2020")
    assert record.ambiguous is True
    assert record.ambiguity_notes


def test_text_without_any_structure_yields_no_records() -> None:
    extraction = extract_profile([source("I like long walks and I once fixed a printer.")])
    assert extraction.records == []
    assert extraction.conflicts == []


# ---- Instructions hidden in a source -----------------------------------------------


def test_instruction_line_in_a_resume_is_not_extracted() -> None:
    expected = json.loads((PROFILES / "injection_expected.json").read_text(encoding="utf-8"))
    text = (PROFILES / "injection_resume.txt").read_text(encoding="utf-8")
    assert expected["injected_line"] in text

    extraction = extract_profile([source(text)])

    output = extraction.model_dump_json()
    for forbidden in expected["must_not_claim"]:
        assert forbidden not in output
    roles = [header(record)[:2] for record in records_of(extraction, "employment")]
    assert roles == [(role["title"], role["employer"]) for role in expected["roles"]]
    # The bullets on either side of the injected line are both still there.
    assert len(records_of(extraction, "employment")[0].bullets) == 3


def test_instruction_line_does_not_change_what_is_extracted() -> None:
    expected = json.loads((PROFILES / "injection_expected.json").read_text(encoding="utf-8"))
    text = (PROFILES / "injection_resume.txt").read_text(encoding="utf-8")
    clean_text = text.replace(expected["injected_line"] + "\n", "")
    assert clean_text != text

    assert extract_profile([source(text)]) == extract_profile([source(clean_text)])


def test_extraction_is_deterministic() -> None:
    assert extract_profile(sample_sources()) == extract_profile(sample_sources())

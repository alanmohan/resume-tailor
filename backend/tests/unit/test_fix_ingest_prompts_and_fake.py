"""Review fixes around the extraction and job-analysis prompts and the fake
extractor: list items under a publication (EM-03), duties and culture
statements in a job analysis (EM-05), status and "Role:" lines (EM-11) and the
prose-heavy fictional fixture (EM-13).

A prompt cannot be unit tested for what a model does with it. These tests pin
the rules the prompt must state, check that the request still carries the
prompt as instructions and the user's text only as data, and test the server
rule that no longer depends on the model following the prompt."""

import json
from pathlib import Path

from app.prompts import load_prompt
from app.providers.base import LLMExtraction, LLMJobAnalysis, LLMJobInput, LLMSource
from app.providers.fake.extraction_ops import analyze_job, extract_profile
from app.services.ingestion import NOTICE_UNCAPTURED, Draft, build_draft
from app.services.job_service import build_requirements
from app.services.textutil import normalize_whitespace
from tests.unit.test_ingest_draft import prepared
from tests.unit.test_ingest_openai_ops import (
    EMPTY_ANALYSIS,
    EMPTY_EXTRACTION,
    StubSDK,
    provider_with,
    single_line,
)
from tests.unit.test_job_analysis import llm_requirement, requirements_of

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
PROSE_TEXT = (FIXTURES / "profiles" / "prose_profile.txt").read_text(encoding="utf-8")
PROSE_EXPECTED = json.loads(
    (FIXTURES / "profiles" / "prose_expected.json").read_text(encoding="utf-8")
)


# ---- Extraction prompt (EM-03, EM-11, SPEC-03) --------------------------------------


def test_extraction_prompt_treats_list_items_under_any_record_as_bullets() -> None:
    prompt = single_line("extraction")
    for rule in (
        "This holds for every category",
        "under a publication, project, achievement, certification or degree",
        "the label line itself is not a bullet",
        "Lose nothing that is written under a record",
        "add any list item or labelled line you have not used",
    ):
        assert rule in prompt, rule


def test_extraction_prompt_shows_a_publication_with_a_result_list() -> None:
    prompt = single_line("extraction")
    assert "### Example" in prompt
    assert "Key results:" in prompt
    # The example names its two result lines as bullets of the publication.
    for bullet in (
        "Cut the median lookup time from 9.1 s to 2.3 s on 1,200 test queries",
        "Raised top-3 accuracy from 0.62 to 0.80",
    ):
        assert prompt.count(bullet) == 2, bullet
    assert "The names below are invented" in prompt


def test_extraction_prompt_keeps_publication_status_and_role_lines() -> None:
    prompt = single_line("extraction")
    for rule in (
        "A publication often has a year or a status in place of a date range",
        "put that text in `start_date` exactly as written",
        "never work out a year that is not written",
        '"Role: Lead developer"',
        "quote the whole line with its label",
    ):
        assert rule in prompt, rule


def test_extraction_prompt_asks_for_every_source_s_contact_values() -> None:
    prompt = single_line("extraction")
    assert "output one item per source, also when the values differ" in prompt


async def test_changed_extraction_prompt_is_sent_as_instructions_and_text_as_data() -> None:
    sdk = StubSDK(EMPTY_EXTRACTION)
    source = LLMSource(alias="S1", label="Career notes", source_type="notes", text=PROSE_TEXT)
    await provider_with(sdk).extract_profile([source])
    request = sdk.requests[0]

    assert request["instructions"] == (
        f"{load_prompt('untrusted_data')}\n\n{load_prompt('extraction')}"
    )
    assert request["text_format"] is LLMExtraction
    assert json.loads(request["input"]) == {"sources": [source.model_dump()]}
    # The example in the prompt is invented; nothing of the user's text is in it.
    for fragment in ("Pellwater", "Corrin Vale", "Floodline"):
        assert fragment not in request["instructions"]


# ---- Job-analysis prompt and the duty rule (EM-05) ----------------------------------


def test_job_prompt_puts_qualifications_first_and_limits_duties() -> None:
    prompt = single_line("job_analysis")
    for rule in (
        "the qualifications the posting states as required, then those it states as "
        "preferred, then duties",
        "only when it names a checkable skill, tool, technology or method",
        "Give it the category `responsibility`",
        "never copy the duty list wholesale",
        "`inferred` is false only for an item the posting states as a qualification",
        "following the posting's own headings and wording",
        "Never promote a preferred item or demote a required one",
        "at most 25",
        "Requirements are unique",
    ):
        assert rule in prompt, rule


def test_job_prompt_excludes_culture_and_benefit_statements() -> None:
    prompt = single_line("job_analysis")
    for rule in (
        "Leave out what cannot be checked against a person's record",
        "values, culture, attitude or personality",
        '"Be deeply curious"',
        "benefits, pay, location",
        "extract only that part",
    ):
        assert rule in prompt, rule


def test_job_prompt_asks_for_the_technical_focus_in_the_role_summary() -> None:
    prompt = single_line("job_analysis")
    assert "Name the technologies and problem areas the posting gives for the role" in prompt
    assert "wherever in the description they are written" in prompt


async def test_changed_job_prompt_is_sent_as_instructions_and_posting_as_data() -> None:
    sdk = StubSDK(EMPTY_ANALYSIS)
    job = LLMJobInput(title="Data Engineer", company="Tarnwick Freight", description="Be curious.")
    await provider_with(sdk).analyze_job(job)
    request = sdk.requests[0]

    assert request["instructions"] == (
        f"{load_prompt('untrusted_data')}\n\n{load_prompt('job_analysis')}"
    )
    assert request["text_format"] is LLMJobAnalysis
    assert json.loads(request["input"]) == {"job": job.model_dump()}
    assert "Tarnwick" not in request["instructions"]


def test_duty_is_stored_as_inferred_even_when_the_model_says_it_is_stated() -> None:
    """Seen with the real model: copied duties came back as stated requirements."""
    description = (
        "Responsibilities\n- Build data pipelines in Spark\n"
        "Requirements\n- 3+ years of Python experience\n"
    )
    stored = requirements_of(
        [
            llm_requirement("3+ years of Python experience", category="experience"),
            llm_requirement("Build data pipelines in Spark", category="responsibility"),
        ],
        description,
    )
    assert [(item.category, item.inferred) for item in stored] == [
        ("experience", False),
        ("responsibility", True),
    ]
    # The duty still points at the passage it was taken from.
    assert stored[1].source_span.excerpt == "Build data pipelines in Spark"


def test_duties_are_cut_before_stated_qualifications_when_the_list_is_capped() -> None:
    duties = [f"Operate service number {n}" for n in range(3)]
    stated = [f"Experience with tool number {n}" for n in range(3)]
    description = "\n".join([*duties, *stated])
    analysis = LLMJobAnalysis(
        role_summary="A role.",
        requirements=[
            *(llm_requirement(text, category="responsibility") for text in duties),
            *(llm_requirement(text) for text in stated),
        ],
    )
    kept = build_requirements(analysis, description, normalize_whitespace(description), 4)
    assert [item.text for item in kept] == [duties[0], *stated]


def test_fake_analysis_reports_duties_as_inferred_responsibilities() -> None:
    posting = json.loads(
        (FIXTURES / "jobs" / "synthetic" / "close_fit.json").read_text(encoding="utf-8")
    )
    analysis = analyze_job(
        LLMJobInput(
            title=posting["title"],
            company=posting["company"],
            description=normalize_whitespace(posting["description"]).text,
        )
    )
    duties = [item for item in analysis.requirements if item.category == "responsibility"]
    stated = [item for item in analysis.requirements if item.category != "responsibility"]
    assert duties and all(item.inferred for item in duties)
    assert stated and not any(item.inferred for item in stated)
    # Importance follows the posting's own headings.
    preferred = {item.text for item in stated if item.importance == "preferred"}
    assert "Experience with AWS" in preferred
    assert "Experience with Docker" not in preferred


# ---- Prose-heavy fictional fixture with the fake extractor (EM-13) ------------------


def prose_draft() -> Draft:
    source = prepared(PROSE_TEXT, label="Career notes")
    extraction = extract_profile(
        [
            LLMSource(
                alias="S1", label="Career notes", source_type="notes", text=source.normalized.text
            )
        ]
    )
    return build_draft(extraction, [source])


def test_prose_fixture_records_match_the_expected_facts() -> None:
    draft = prose_draft()
    assert len(draft.records) == len(PROSE_EXPECTED["records"])

    for record, facts in zip(draft.records, PROSE_EXPECTED["records"], strict=True):
        assert (record.category, record.title, record.organization) == (
            facts["category"],
            facts["title"],
            facts["organization"],
        )
        dates = " - ".join(date for date in (record.start_date, record.end_date) if date) or None
        assert dates == facts["date_string"]
        assert record.skills == facts["skills"]
        assert [bullet.text for bullet in record.bullets] == facts["statements"]
        assert record.needs_review is False, record.review_reasons
        # Every statement points at exactly the text it quotes.
        for bullet in record.bullets:
            ref = bullet.source_ref
            assert PROSE_TEXT[ref.start : ref.end] == ref.excerpt == bullet.text


def test_prose_fixture_keeps_every_summary_paragraph() -> None:
    role = prose_draft().records[0]
    starts = PROSE_EXPECTED["records"][0]["summary_paragraph_starts"]
    assert len(starts) == 2
    positions = [role.summary.index(start) for start in starts]
    assert positions == sorted(positions) and positions[0] == 0
    # The summary is the source's own wording: both paragraphs are in the text.
    paragraphs = [line for line in PROSE_TEXT.splitlines() if line.startswith(tuple(starts))]
    assert role.summary == " ".join(paragraphs)


def test_prose_fixture_publication_keeps_its_result_list_status_and_role() -> None:
    publication = next(r for r in prose_draft().records if r.category == "publication")
    statements = [bullet.text for bullet in publication.bullets]
    assert statements[:2] == [
        "Status: Accepted, presented as a contributed talk",
        "Role: First author",
    ]
    assert len(statements[2:]) == 3
    assert publication.start_date == "2024"
    # The list label is not a statement.
    assert not any(text.endswith(":") for text in statements)


def test_prose_fixture_is_fully_captured_and_only_lacks_contact_and_education() -> None:
    draft = prose_draft()
    assert draft.contact.model_dump() == PROSE_EXPECTED["contact"]
    assert [notice.code for notice in draft.notices] == PROSE_EXPECTED["expected_notice_codes"]
    assert NOTICE_UNCAPTURED not in {notice.code for notice in draft.notices}
    assert draft.conflicts == []

    # Every list line of the source ended up as a statement of some record.
    statements = {bullet.text for record in draft.records for bullet in record.bullets}
    listed = [line[2:] for line in PROSE_TEXT.splitlines() if line.startswith("- ")]
    assert len(listed) == 10
    assert set(listed) <= statements

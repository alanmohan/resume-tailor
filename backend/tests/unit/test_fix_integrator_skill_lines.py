"""A whole line of a skills section returned as one skill is read as the skills
it lists.

Found in the second real evaluation run: for a profile that writes its
technologies under each role as labelled lines ("Backend & Cloud: Python,
Flask, AWS S3."), the model returned every line as a single skill. The resume's
skills line then showed whole lines, one line was dropped for its length, and
the individual technologies could not be listed or cited as skills.
"""

import pytest

from app.services.ingestion import NOTICE_UNCAPTURED, skill_items
from tests.unit.test_ingest_draft import HEADER, draft_of, llm_record, prepared

SECTION = (
    "Mira Okafor\n"
    "Experience\n"
    f"{HEADER}\n"
    "- Built Python pipelines for 12 analysts\n"
    "Key Technologies & Skills\n"
    "Backend & Cloud: Python, Flask, AWS (S3, SNS), REST APIs.\n"
    "Explainable AI (XAI): SHAP (Kernel Explainer), Feature Importance Analysis.\n"
    "Data & Automation: python-docx, openpyxl, pypdf, pikepdf, Multi-threading/Concurrency, "
    "Data Extraction & Parsing, Web Scraping (Order Status API), PDF Merging and Splitting.\n"
    "Coursework only: TensorFlow, Keras\n"
    "Spoken languages: English\n"
)
LINES = [line for line in SECTION.splitlines() if ": " in line]


@pytest.mark.parametrize(
    ("entry", "items"),
    [
        (
            "Backend & Cloud: Python, Flask, AWS (S3, SNS), REST APIs.",
            ["Python", "Flask", "AWS (S3, SNS)", "REST APIs"],
        ),
        # A label may carry a note in brackets of its own.
        (
            "Explainable AI (XAI): SHAP (Kernel Explainer), Feature Importance Analysis.",
            ["SHAP (Kernel Explainer)", "Feature Importance Analysis"],
        ),
        ("Languages: Python; SQL, and Go", ["Python", "SQL", "Go"]),
    ],
)
def test_labelled_line_is_split_into_the_skills_it_lists(entry: str, items: list[str]) -> None:
    assert skill_items(entry) == items


@pytest.mark.parametrize(
    "entry",
    [
        "Python",
        "AWS (S3, EC2, Lambda)",
        "LLM evaluation (RAGAS, LLM-as-judge, agentic metrics)",
        # One item: cannot be told apart from a skill with a colon in its name.
        "Spoken languages: English",
        "ISO 27001: lead auditor",
        # The label is a qualifier of the skills and must stay with them.
        "Coursework only: TensorFlow, Keras",
        "Basic familiarity: Rust, Zig",
    ],
)
def test_ordinary_and_qualified_skills_stay_whole(entry: str) -> None:
    assert skill_items(entry) == [entry]


def test_lines_returned_as_skills_become_individual_skills_of_the_record() -> None:
    record = llm_record(bullets=[], skills=LINES)
    draft = draft_of([record], [prepared(SECTION)])

    (role,) = draft.records
    assert role.skills == [
        "Python",
        "Flask",
        "AWS (S3, SNS)",
        "REST APIs",
        "SHAP (Kernel Explainer)",
        "Feature Importance Analysis",
        "python-docx",
        "openpyxl",
        "pypdf",
        "pikepdf",
        "Multi-threading/Concurrency",
        "Data Extraction & Parsing",
        "Web Scraping (Order Status API)",
        "PDF Merging and Splitting",
        "Coursework only: TensorFlow, Keras",
        "Spoken languages: English",
    ]
    # The third line is longer than a skill may be; split, nothing of it is lost.
    assert len(LINES[2]) > 120
    assert not any("longer than" in reason for reason in role.review_reasons)


def test_a_standalone_group_of_such_lines_is_split_too_and_counts_as_captured() -> None:
    group = llm_record(
        category="skill",
        title="Key Technologies & Skills",
        organization=None,
        start_date=None,
        end_date=None,
        header_quote="Key Technologies & Skills",
        skills=LINES[:2],
    )
    draft = draft_of([llm_record(), group], [prepared(SECTION)])

    skills = next(record for record in draft.records if record.category == "skill").skills
    assert skills[:4] == ["Python", "Flask", "AWS (S3, SNS)", "REST APIs"]
    assert "Backend & Cloud: Python, Flask, AWS (S3, SNS), REST APIs." not in skills
    # The two lines whose items became skills are not reported as lost text.
    messages = " ".join(n.message for n in draft.notices if n.code == NOTICE_UNCAPTURED)
    assert "Backend & Cloud" not in messages
    assert "Explainable AI" not in messages


def test_an_item_that_is_not_in_the_source_is_still_left_out() -> None:
    record = llm_record(skills=["Backend & Cloud: Python, Flask, Kubernetes"])
    draft = draft_of([record], [prepared(SECTION)])

    (role,) = draft.records
    assert role.skills == ["Python", "Flask"]
    assert role.needs_review is True
    assert 'The skill "Kubernetes" was left out: it is not in the source text.' in (
        role.review_reasons
    )

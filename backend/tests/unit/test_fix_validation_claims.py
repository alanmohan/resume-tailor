"""Regression tests for the review findings in the grounding validators
(G-06, G-07, G-03, G-08, G-10). Each finding is tested in both directions: the
dishonest statement is caught and the honest paraphrase still passes."""

import pytest

from app.services.validation import (
    ClaimVerdict,
    Finding,
    GroundingContext,
    Vocabulary,
    escalation_findings,
    find_numbers,
    name_tokens,
    number_findings,
    requirement_terms,
    separate_units,
    singular_tokens,
    validate_claim,
)

LATENCY = (
    "Software Engineer at Quillfeather Software: Reduced median API response time from "
    "420 ms to 290 ms by adding Redis caching and PostgreSQL indexes"
)
IMAGES = (
    "Machine Learning Engineer at Brightloom Labs: Packaged model services as Docker "
    "images, cutting image size from 1.4 GB to 380 MB with multi-stage builds"
)
COVERAGE = (
    "Improved unit test coverage by 20% by adding pytest suites for the billing and export modules"
)
DASHBOARDS = "Built React and TypeScript dashboards used by 35 internal support agents"
RAG = (
    "Machine Learning Engineer at Brightloom Labs: Built a retrieval-augmented generation "
    "(RAG) service in Python and FastAPI over 40,000 help-center articles"
)
PROFILE = [LATENCY, IMAGES, COVERAGE, DASHBOARDS, RAG]

SOMETHING_ELSE = '"20%" appears in the cited evidence, but about something else.'


def context(
    *keywords: str, title: str = "Platform Engineer", company: str = "Fernhollow AI"
) -> GroundingContext:
    """Grounding for the fictional profile above and a job with these keywords."""
    terms: set[str] = set()
    for keyword in keywords:
        terms.update(requirement_terms(keyword, [keyword]))
    return GroundingContext(
        profile=Vocabulary.of(PROFILE),
        requirement_keywords=frozenset(terms),
        letter_terms=name_tokens(title, company, "Jordan Rivera"),
        job_names=(title, company),
    )


def check(claim: str, cited: list[str], grounding: GroundingContext | None = None, **options):
    options.setdefault("section", "experience")
    return validate_claim(claim, cited, grounding or context(), **options)


# ---- G-06: figures written together with their unit --------------------------------


def test_units_are_written_apart_from_their_figure() -> None:
    assert separate_units("from 420ms to 290ms") == "from 420 ms to 290 ms"
    assert separate_units("1.4GB down to 380MB at 10Gbps") == "1.4 GB down to 380 MB at 10 Gbps"
    # Names that contain digits are not measurements.
    names = "p95 on S3 with IPv6, a 3D model, 2FA, the 2nd region and OAuth2"
    assert separate_units(names) == names


def test_figure_with_attached_unit_matches_the_spaced_evidence() -> None:
    claim = (
        "Reduced median API response time from 420ms to 290ms by adding Redis caching "
        "and PostgreSQL indexes"
    )
    assert check(claim, [LATENCY]) == ClaimVerdict("supported", [])
    claim = (
        "Packaged model services as Docker images, cutting image size from 1.4GB to 380MB "
        "with multi-stage builds"
    )
    assert check(claim, [IMAGES]) == ClaimVerdict("supported", [])


def test_spaced_figure_matches_evidence_written_with_an_attached_unit() -> None:
    evidence = "Cut cold-start time from 900ms to 150ms and memory use from 2GB to 512MB"
    grounding = GroundingContext(
        profile=Vocabulary.of([evidence]),
        requirement_keywords=frozenset(),
        letter_terms=frozenset(),
    )
    claim = "Cut cold-start time from 900 ms to 150 ms and memory use from 2 GB to 512 MB"
    assert check(claim, [evidence], grounding) == ClaimVerdict("supported", [])
    assert "gb" in singular_tokens(evidence)


def test_invented_figure_with_attached_unit_is_rejected_as_a_figure() -> None:
    result = check("Reduced median API response time from 420ms to 90ms", [LATENCY])
    assert result.status == "unsupported"
    assert result.warnings == ['"90" does not appear in the cited evidence.']


def test_decimal_with_an_unknown_unit_does_not_yield_its_integer_part() -> None:
    """ "1.4XB" used to be read as the figure "1" followed by ".4XB"."""
    assert find_numbers("grew to 1.4XB and version 1.2.3") == []
    assert [mention.raw for mention in find_numbers("grew 1.4 times, to 3.5")] == [
        "1.4 times",
        "3.5",
    ]


@pytest.mark.parametrize(
    ("claim", "evidence"),
    [
        ("Indexed 40k help-center articles for the RAG service", RAG),
        ("Indexed 40K help-center articles for the RAG service", RAG),
        ("Indexed 40 thousand help-center articles for the RAG service", RAG),
        ("Improved unit test coverage by 20 percent with pytest suites", COVERAGE),
        ("Improved unit test coverage by twenty percent with pytest suites", COVERAGE),
        ("Improved unit test coverage by 20 per cent with pytest suites", COVERAGE),
        ("Improved unit test coverage by 20 % with pytest suites", COVERAGE),
        ("Processed 1,200,000 log lines per day", "Processed 1.2M log lines per day"),
        ("Processed 1.2 million log lines per day", "Processed 1,200,000 log lines per day"),
    ],
)
def test_same_value_in_another_notation_is_not_rejected(claim: str, evidence: str) -> None:
    assert number_findings(claim, [evidence]) == []


def test_another_value_in_the_same_notation_is_still_rejected() -> None:
    assert number_findings("Indexed 400k help-center articles", [RAG]) != []
    assert number_findings("Improved unit test coverage by 25 percent", [COVERAGE]) != []


# ---- G-07: fragments of compound job keywords --------------------------------------


def test_descriptor_compounds_leave_no_requirement_terms() -> None:
    terms = requirement_terms(
        "Front-end experience with React and TypeScript, hands-on, in real-time systems",
        ["front-end", "react", "typescript", "full-stack", "hands-on", "real-time", "large-scale"],
    )
    assert terms == {"react": "React", "typescript": "TypeScript"}


def test_honest_bullet_with_a_descriptor_from_the_posting_is_supported() -> None:
    grounding = context("front-end", "react", "typescript", "real-time", "large-scale")
    claim = "Built front-end dashboards in React and TypeScript used by 35 internal support agents"
    assert check(claim, [DASHBOARDS], grounding) == ClaimVerdict("supported", [])
    claim = "Reduced median API response time from 420 ms to 290 ms at large scale"
    assert check(claim, [LATENCY], grounding) == ClaimVerdict("supported", [])


def test_distinctive_compound_keyword_is_still_required() -> None:
    assert requirement_terms("Experience with React Native", ["react native"]) == {
        "native": "Native",
        "react": "React",
    }
    grounding = context("react native", "machine learning", "front-end")
    invented = "Built front-end screens in react native used by 35 internal support agents"
    result = check(invented, [DASHBOARDS], grounding)
    assert result.status == "unsupported"
    assert result.warnings == ['"native" does not appear anywhere in your confirmed profile.']


# ---- G-03: words of the job title in the cover letter ------------------------------

KUBERNETES_JOB = {"title": "Kubernetes Platform Engineer", "company": "Fernhollow AI"}


def test_skill_named_in_the_job_title_is_not_unlocked_in_the_cover_letter() -> None:
    grounding = context("kubernetes", **KUBERNETES_JOB)
    with_evidence = check(
        "At Brightloom Labs I packaged model services as Docker images and ran them on "
        "Kubernetes in production.",
        [IMAGES],
        grounding,
        section="cover_letter",
    )
    assert with_evidence.status == "unsupported"
    assert with_evidence.warnings == [
        '"Kubernetes" does not appear anywhere in your confirmed profile.'
    ]

    connective = check(
        "My Kubernetes background makes me a good match for this team.",
        [],
        grounding,
        section="cover_letter",
        factual=False,
    )
    assert connective.status == "needs_review"
    assert '"Kubernetes"' in connective.warnings[0]


def test_literal_job_title_and_company_may_be_named_in_the_cover_letter() -> None:
    grounding = context("kubernetes", **KUBERNETES_JOB)
    greeting = (
        "Dear Hiring Manager, I am writing to apply for the Kubernetes Platform Engineer "
        "role at Fernhollow AI."
    )
    assert check(greeting, [], grounding, section="cover_letter", factual=False) == ClaimVerdict(
        "not_applicable", []
    )
    paragraph = (
        "My work packaging model services as Docker images prepares me for the Kubernetes "
        "Platform Engineer role at Fernhollow AI."
    )
    assert check(paragraph, [IMAGES], grounding, section="cover_letter") == ClaimVerdict(
        "supported", []
    )


def test_title_words_that_claim_no_missing_skill_stay_allowed_in_the_cover_letter() -> None:
    """A shortened title ("the Platform Engineer role") and a title skill the
    profile does have ("Docker") are not claims of something new."""
    grounding = context(
        "kubernetes", "docker", title="Docker and Kubernetes Platform Engineer, Core Systems"
    )
    paragraph = "As a Platform Engineer at Fernhollow I would keep packaging Docker images."
    assert check(paragraph, [IMAGES], grounding, section="cover_letter").status == "supported"


def test_job_name_is_only_set_aside_as_a_whole_word() -> None:
    """A company called "Kube" must not blank the start of "Kubernetes"."""
    grounding = context("kubernetes", title="Platform Engineer", company="Kube")
    result = check(
        "My Kubernetes background makes me a good match for Kube.",
        [],
        grounding,
        section="cover_letter",
        factual=False,
    )
    assert result.status == "needs_review"
    assert '"Kubernetes"' in result.warnings[0]


# ---- G-08: what a figure measures --------------------------------------------------


@pytest.mark.parametrize(
    "claim",
    [
        "Reduced cloud costs by 20% by adding pytest suites",
        "Cut regression bugs by 20% by adding pytest suites for the billing and export modules",
        "Reduced cloud costs by 20% through rightsizing",
        "Reduced cloud costs by 20% by improving unit test coverage",
    ],
)
def test_figure_moved_to_another_result_is_rejected_even_with_the_same_method(claim: str) -> None:
    assert number_findings(claim, [COVERAGE]) == [Finding("unsupported", SOMETHING_ELSE)]


@pytest.mark.parametrize(
    "claim",
    [
        "Improved unit test coverage by 20% by adding pytest suites for billing and export",
        "Improved unit test coverage by 20 percent by adding pytest suites",
        "Raised unit test coverage twenty percent with pytest suites for billing and export",
        "Added pytest suites for the billing and export modules, lifting test coverage 20%",
        "Achieved a 20% gain in unit test coverage through new pytest suites",
    ],
)
def test_honest_rewordings_of_the_measured_result_are_accepted(claim: str) -> None:
    assert number_findings(claim, [COVERAGE]) == []


def test_method_clause_is_not_what_a_figure_measures_in_other_constructions() -> None:
    evidence = "Cut build time 30% using a shared cache"
    assert number_findings("Cut cloud spend 30% using a shared cache", [evidence]) != []
    assert number_findings("Shortened build time 30% with a shared cache", [evidence]) == []
    # What is measured may also follow the figure.
    evidence = "Achieved a 30% reduction in build time by sharing a cache"
    assert number_findings("Achieved a 30% reduction in cloud spend by sharing a cache", [evidence])
    assert number_findings("Reduced build time by 30%", [evidence]) == []


def test_statement_that_only_says_how_is_compared_on_the_method() -> None:
    """ "A 20% saving through rightsizing" names no result that could differ
    from the evidence, so its method has to be the evidence's method."""
    costs = "Reduced cloud costs by 20% by rightsizing instances."
    assert number_findings("Achieved a 20% saving through rightsizing instances", [costs]) == []
    assert number_findings("Achieved 20% by adding pytest suites", [COVERAGE]) == []
    # A different result hidden in the method clause does not get through.
    assert number_findings("Achieved 20% by cutting cloud costs", [COVERAGE]) == [
        Finding("unsupported", SOMETHING_ELSE)
    ]
    vague = "Gained 15% by caching thumbnails"
    assert number_findings("Gained 15% by caching thumbnails", [vague]) == []
    assert number_findings("Gained 15% by rewriting the billing module", [vague]) != []


# ---- G-10: seniority -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("claim", "phrase"),
    [
        (
            "Senior machine learning engineer who built a RAG service in Python and FastAPI",
            "Senior",
        ),
        ("Built a RAG service in Python and FastAPI as principal architect", "principal"),
        ("Lead engineer who built a RAG service in Python and FastAPI", "Lead engineer"),
        ("Built a RAG service in Python and FastAPI as tech lead", "tech lead"),
    ],
)
def test_seniority_not_in_the_evidence_needs_review(claim: str, phrase: str) -> None:
    result = check(claim, [RAG], section="summary")
    assert result.status == "needs_review"
    assert result.warnings == [
        f'"{phrase}" is stronger or more specific than the wording of the cited evidence.'
    ]


def test_seniority_stated_by_the_evidence_or_its_record_title_is_supported() -> None:
    senior_role = "Senior Machine Learning Engineer at Brightloom Labs: Built a RAG service"
    assert escalation_findings("Senior engineer who built a RAG service", [senior_role]) == []
    lead_role = "Brightloom Labs: Lead engineer for the support assistant, built a RAG service"
    assert escalation_findings("Lead engineer for a RAG service", [lead_role]) == []
    # "lead" as a verb or a noun about something else is not a title.
    assert escalation_findings("Helped lead the rollout that cut lead time", [RAG]) == []


def test_seniority_in_the_jobs_own_title_is_not_a_claim_in_the_cover_letter() -> None:
    grounding = context(title="Senior Platform Engineer, ML Infrastructure")
    greeting = (
        "Dear Hiring Manager, I am writing to apply for the Senior Platform Engineer, "
        "ML Infrastructure role at Fernhollow AI."
    )
    assert check(greeting, [], grounding, section="cover_letter", factual=False) == ClaimVerdict(
        "not_applicable", []
    )
    shortened = "My Python and FastAPI work fits the Senior Platform Engineer role."
    assert check(shortened, [RAG], grounding, section="cover_letter").status == "supported"

    # Outside the cover letter the job's title is no excuse.
    in_resume = check(
        "Senior engineer who built a RAG service", [RAG], grounding, section="summary"
    )
    assert in_resume.status == "needs_review"
    # A level the job's title does not name is still a claim in the letter.
    inflated = check(
        "As a principal engineer I built a RAG service in Python.",
        [RAG],
        grounding,
        section="cover_letter",
    )
    assert inflated.status == "needs_review"

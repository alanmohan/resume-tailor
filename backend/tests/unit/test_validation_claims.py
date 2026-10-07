"""The deterministic grounding validators. Each test states a claim, the
evidence it cites and the verdict a careful reader would expect."""

from typing import Any

import pytest

from app.providers.base import LLMClaimCheck, LLMVerdict, LLMVerification, Usage
from app.schemas.generations import Claim, CoverLetter, ResumeDocument, ResumeEntry
from app.schemas.profiles import Contact
from app.services.validation import (
    NO_EVIDENCE_MESSAGE,
    ClaimVerdict,
    Finding,
    GroundingContext,
    Vocabulary,
    escalation_findings,
    evidence_mentioning,
    find_numbers,
    find_terms,
    iter_claims,
    name_tokens,
    number_findings,
    requirement_terms,
    semantic_findings,
    singular_tokens,
    spell_numbers_as_digits,
    split_known_ids,
    stricter,
    summarize_validation,
    validate_claim,
)

DOCKER_EVIDENCE = "Software Engineer at Quillfeather Software: Containerised services with Docker."
COST_EVIDENCE = "Reduced cloud costs by 20% by rightsizing instances."
LATENCY_EVIDENCE = "Improved API latency by adding Redis caching."
PROFILE_TEXTS = [
    DOCKER_EVIDENCE,
    COST_EVIDENCE,
    LATENCY_EVIDENCE,
    "Skills: Python, Docker, Terraform, Redis",
]


def grounding(*requirement_keywords: str) -> GroundingContext:
    """The sample profile mentions Docker and Terraform but never Kubernetes."""
    keywords: set[str] = set()
    for keyword in requirement_keywords:
        keywords.update(requirement_terms(keyword, [keyword]))
    return GroundingContext(
        profile=Vocabulary.of(PROFILE_TEXTS),
        requirement_keywords=frozenset(keywords),
        letter_terms=name_tokens("Platform Engineer", "Globex", "Jordan Rivera"),
    )


def verdict(claim: str, cited: list[str], **options: Any) -> ClaimVerdict:
    options.setdefault("section", "experience")
    keywords = options.pop("keywords", ())
    return validate_claim(claim, cited, grounding(*keywords), **options)


# ---- (a) evidence-ID membership ----------------------------------------------------


def test_citations_outside_the_issued_set_are_separated() -> None:
    issued = {"E1": "id-1", "E2": "id-2"}
    cited = ["E2", "E9", "E1", "E2", "4f6c0d7a9b0e4d0a8c3b2a1908f7e6d5", "E9"]
    assert split_known_ids(cited, issued) == (
        ["E2", "E1"],
        ["E9", "4f6c0d7a9b0e4d0a8c3b2a1908f7e6d5"],
    )


def test_factual_claim_without_valid_evidence_is_unsupported() -> None:
    result = verdict("Containerised services with Docker.", [])
    assert result == ClaimVerdict("unsupported", [NO_EVIDENCE_MESSAGE])


# ---- (b) numbers with unit and context ---------------------------------------------


def test_numbers_are_read_with_value_unit_and_open_endedness() -> None:
    text = spell_numbers_as_digits(
        "Cut costs 20%, served 5,000+ users, 3x faster, saved $2M, twenty-five percent, "
        "led five engineers, over 300 sites, 10k requests"
    )
    found = {
        mention.raw: (mention.value, mention.unit, mention.at_least)
        for mention in find_numbers(text)
    }
    assert found == {
        "20%": (20.0, "%", False),
        "5,000+": (5000.0, "", True),
        "3x": (3.0, "x", False),
        "$2M": (2_000_000.0, "$", False),
        "25 percent": (25.0, "%", False),
        "5": (5.0, "", False),
        "300": (300.0, "", True),
        "10k": (10_000.0, "", False),
    }


def test_identifiers_and_ordinals_are_not_numbers() -> None:
    assert find_numbers("p95 latency on S3 in the 2nd region with IPv6 and 100ms timeouts") == []


def test_number_with_matching_context_is_supported() -> None:
    assert number_findings("Cut cloud costs 20% through rightsizing.", [COST_EVIDENCE]) == []
    assert verdict("Reduced cloud costs by 20%.", [COST_EVIDENCE]).status == "supported"


def test_twenty_percent_from_an_unrelated_statement_is_rejected() -> None:
    """The spec's example: the cited evidence does contain "20%", but about
    cloud costs, not about API latency."""
    result = verdict(
        "Improved API latency by 20% by adding Redis caching.", [COST_EVIDENCE, LATENCY_EVIDENCE]
    )
    assert result.status == "unsupported"
    assert result.warnings == ['"20%" appears in the cited evidence, but about something else.']


def test_number_absent_from_the_cited_evidence_is_rejected() -> None:
    result = verdict("Reduced cloud costs by 35%.", [COST_EVIDENCE])
    assert result.status == "unsupported"
    assert result.warnings == ['"35%" does not appear in the cited evidence.']


def test_number_present_only_in_uncited_evidence_is_rejected() -> None:
    result = verdict("Improved API latency by 20%.", [LATENCY_EVIDENCE])
    assert result.status == "unsupported"
    assert '"20%" does not appear in the cited evidence.' in result.warnings


def test_same_value_with_another_unit_is_rejected() -> None:
    findings = number_findings("Reduced cloud costs by $20.", [COST_EVIDENCE])
    assert findings == [Finding("unsupported", '"$20" does not appear in the cited evidence.')]


def test_open_ended_number_needs_open_ended_evidence() -> None:
    assert number_findings("Served 5,000+ users.", ["Served 5,000 users."]) != []
    assert number_findings("Served 5,000+ users.", ["Served over 5,000 users."]) == []
    assert number_findings("Served 5,000 users.", ["Served 5,000+ users."]) == []


def test_spelled_out_and_abbreviated_numbers_match_digits() -> None:
    assert number_findings("Led a team of 5 engineers.", ["Led a team of five engineers."]) == []
    assert number_findings("Processed 5k events daily.", ["Processed 5,000 events per day."]) == []
    assert number_findings("Saved $2 million in costs.", ["Cut costs by $2M."]) == []


def test_number_without_describing_words_is_accepted_when_it_is_in_the_evidence() -> None:
    assert number_findings("Achieved a 20% improvement.", [COST_EVIDENCE]) == []


def test_context_is_taken_from_the_evidence_sentence_holding_the_number() -> None:
    evidence = "Reduced cloud costs by 20%. Improved API latency with caching."
    assert number_findings("Improved API latency by 20%.", [evidence]) != []


def test_only_the_words_next_to_the_evidence_figure_say_what_it_measures() -> None:
    evidence = (
        "Improved unit test coverage by 20% by adding pytest suites for the billing "
        "and export modules"
    )
    # "bill" occurs in the evidence, but in the clause about how the result was
    # achieved, not next to the figure.
    assert number_findings("Lowered the AWS bill by 20%.", [evidence]) == [
        Finding("unsupported", '"20%" appears in the cited evidence, but about something else.')
    ]
    assert number_findings("Raised test coverage by 20% with pytest.", [evidence]) == []
    # Figures are passed over when looking for the neighbouring words.
    latency = "Reduced median API response time from 420 ms to 290 ms by adding Redis caching"
    assert number_findings("Cut API response time to 290 ms.", [latency]) == []


# ---- (c) technology names, proper nouns and requirement keywords -------------------


def test_terms_are_names_and_requirement_keywords() -> None:
    text = "Kubernetes clusters ran Docker and PostgreSQL. Built CI/CD with c++ for testing."
    terms = {term.raw: term.name_shaped for term in find_terms(text, frozenset({"testing"}))}
    assert terms == {
        "Docker": True,
        "PostgreSQL": True,
        "CI": True,
        "CD": True,
        "c++": True,
        "testing": False,
    }
    # A plain capitalised first word is only a term when the job asks for it.
    with_keyword = find_terms(text, frozenset({"kubernete"}))
    assert with_keyword[0].raw == "Kubernetes"


def test_kubernetes_claim_is_unsupported_when_the_profile_only_mentions_docker() -> None:
    result = verdict(
        "Deployed services to Kubernetes with Docker.", [DOCKER_EVIDENCE], keywords=["kubernetes"]
    )
    assert result.status == "unsupported"
    assert result.warnings == ['"Kubernetes" does not appear anywhere in your confirmed profile.']


def test_lower_case_requirement_keyword_is_caught_too() -> None:
    result = verdict(
        "kubernetes deployments were automated with Docker.",
        [DOCKER_EVIDENCE],
        keywords=["kubernetes"],
    )
    assert result.status == "unsupported"


def test_term_elsewhere_in_the_profile_needs_review() -> None:
    result = verdict("Provisioned Docker hosts with Terraform.", [DOCKER_EVIDENCE])
    assert result.status == "needs_review"
    assert result.warnings == ['"Terraform" is in your profile but not in the evidence cited here.']


def test_terms_of_the_cited_evidence_are_supported() -> None:
    assert verdict("Containerised services using Docker.", [DOCKER_EVIDENCE]).status == "supported"
    # The role and employer of the cited evidence count as evidence too.
    claim = "As a Software Engineer at Quillfeather Software, containerised services."
    assert verdict(claim, [DOCKER_EVIDENCE]).status == "supported"


def test_plural_and_inflected_forms_do_not_cause_false_alarms() -> None:
    evidence = ["Built a REST API and tested data pipelines."]
    context = GroundingContext(
        profile=Vocabulary.of(evidence),
        requirement_keywords=frozenset(requirement_terms("Testing of APIs", ["testing", "APIs"])),
        letter_terms=frozenset(),
    )
    result = validate_claim(
        "Built REST APIs and a pipeline, with testing.", evidence, context, section="experience"
    )
    assert result == ClaimVerdict("supported", [])


def test_name_used_as_a_verb_is_backed_by_the_name_but_lookalikes_are_not() -> None:
    evidence = ["Packaged services as Docker images in the cloud."]
    context = GroundingContext(
        profile=Vocabulary.of(evidence), requirement_keywords=frozenset(), letter_terms=frozenset()
    )

    def check(claim: str) -> str:
        return validate_claim(claim, evidence, context, section="experience").status

    assert check("Shipped Dockerized services to the cloud.") == "supported"
    assert check("Shipped services to Cloudflare.") == "unsupported"
    assert check("Shipped services with Dockerfile linting by Dockerhub.") == "unsupported"


def test_invented_employer_is_unsupported() -> None:
    result = verdict("Led the platform group at Initech.", [DOCKER_EVIDENCE])
    assert result.status == "unsupported"
    assert '"Initech" does not appear anywhere in your confirmed profile.' in result.warnings


def test_job_title_and_company_are_allowed_in_the_cover_letter_only() -> None:
    claim = "At Globex I would containerise services with Docker as a Platform Engineer."
    assert verdict(claim, [DOCKER_EVIDENCE], section="cover_letter").status == "supported"
    assert verdict(claim, [DOCKER_EVIDENCE], section="summary").status == "unsupported"


def test_generic_posting_words_are_not_requirement_keywords() -> None:
    terms = requirement_terms(
        "Strong experience with Kubernetes", ["experience", "Kubernetes", "5+"]
    )
    assert terms == {"kubernete": "Kubernetes"}
    assert requirement_terms("Proven ability to work in a team", []) == {}


# ---- (d) expertise and seniority escalation ----------------------------------------


@pytest.mark.parametrize(
    "claim",
    [
        "Expert in Docker.",
        "Extensive experience with Docker.",
        "Advanced Docker user.",
        "5+ years of Docker experience.",
        "Deep expertise in Docker.",
    ],
)
def test_escalation_not_in_the_evidence_needs_review(claim: str) -> None:
    result = verdict(claim, [DOCKER_EVIDENCE])
    assert result.status == "needs_review"
    assert "stronger or more specific than the wording of the cited evidence" in result.warnings[0]


def test_escalation_wording_used_by_the_evidence_is_supported() -> None:
    evidence = ["Docker expert with 5+ years of extensive experience in containers."]
    assert escalation_findings("Expert in Docker with 5+ years of experience.", evidence) == []
    assert escalation_findings("Extensive experience with containers.", evidence) == []
    assert escalation_findings("Docker user for five+ years.", evidence) == []


def test_years_of_service_are_reviewed_not_removed() -> None:
    """A length of service may be true even when no evidence states it, so it
    is flagged for the user instead of being deleted."""
    result = verdict("Used Docker for 6 years.", [DOCKER_EVIDENCE])
    assert result.status == "needs_review"


# ---- (e) skills --------------------------------------------------------------------


def test_skill_must_be_mentioned_by_some_evidence_record() -> None:
    tokens = {
        "ev-bullet": singular_tokens("Built REST APIs in Python"),
        "ev-skills": singular_tokens("Skills: Python, Docker, Machine Learning"),
    }
    assert evidence_mentioning("Python", tokens, preferred=[]) == ["ev-bullet", "ev-skills"]
    assert evidence_mentioning("Python", tokens, preferred=["ev-skills"]) == [
        "ev-skills",
        "ev-bullet",
    ]
    assert evidence_mentioning("REST API", tokens, preferred=[]) == ["ev-bullet"]
    assert evidence_mentioning("machine learning", tokens, preferred=["unknown"]) == ["ev-skills"]
    assert evidence_mentioning("Kubernetes", tokens, preferred=[]) == []
    # Both words must be in the same record.
    assert evidence_mentioning("Docker APIs", tokens, preferred=[]) == []
    assert evidence_mentioning("...", tokens, preferred=[]) == []


# ---- (f) connective text -----------------------------------------------------------


def test_plain_connective_text_is_not_applicable() -> None:
    for text in (
        "Dear Hiring Manager, I am writing to apply for the Platform Engineer role at Globex.",
        "Thank you for considering my application.",
        "Sincerely, Jordan Rivera",
    ):
        result = verdict(text, [], section="cover_letter", factual=False)
        assert result == ClaimVerdict("not_applicable", []), text


def test_connective_label_does_not_hide_a_claim() -> None:
    hidden_skill = verdict(
        "I bring deep expertise in Kubernetes.", [], section="cover_letter", factual=False
    )
    assert hidden_skill.status == "needs_review"
    assert any("Kubernetes" in warning for warning in hidden_skill.warnings)
    assert any("deep expertise" in warning for warning in hidden_skill.warnings)

    hidden_number = verdict(
        "I grew revenue by 40% last year.", [], section="cover_letter", factual=False
    )
    assert hidden_number.status == "unsupported"


def test_a_figure_in_the_job_title_or_company_is_a_name_not_a_claim() -> None:
    numbered_job = GroundingContext(
        profile=Vocabulary.of(PROFILE_TEXTS),
        requirement_keywords=frozenset(),
        letter_terms=name_tokens("Platform Engineer 2", "Studio 54", "Jordan Rivera"),
        job_names=("Platform Engineer 2", "Studio 54"),
    )

    def check(text: str, cited: list[str], *, factual: bool) -> ClaimVerdict:
        return validate_claim(text, cited, numbered_job, section="cover_letter", factual=factual)

    greeting = "Dear Hiring Manager, I am applying for the Platform Engineer 2 role at Studio 54."
    assert check(greeting, [], factual=False) == ClaimVerdict("not_applicable", [])

    # The same figures outside the job's name are still claims without evidence.
    assert check("I led 2 teams and 54 releases.", [], factual=False).status == "unsupported"

    # With evidence, the job's name is set aside and every other figure is checked.
    fits = "My Docker work prepares me for the Platform Engineer 2 role at Studio 54."
    assert check(fits, [DOCKER_EVIDENCE], factual=True).status == "supported"
    invented = "At Studio 54 I would repeat the 54% saving I made with Docker."
    assert check(invented, [DOCKER_EVIDENCE], factual=True).status == "unsupported"

    # A resume statement may not name the job at all, so nothing is set aside there.
    in_resume = validate_claim(
        "Platform Engineer 2 work with Docker.", [DOCKER_EVIDENCE], numbered_job, section="summary"
    )
    assert in_resume.status == "unsupported"


def test_connective_text_may_repeat_posting_words_only_if_the_profile_has_them() -> None:
    keywords = ["docker", "kubernetes"]
    about_the_job = verdict(
        "I look forward to working on your docker platform.",
        [],
        section="cover_letter",
        factual=False,
        keywords=keywords,
    )
    assert about_the_job == ClaimVerdict("not_applicable", [])

    hidden_claim = verdict(
        "My kubernetes skills match this role.",
        [],
        section="cover_letter",
        factual=False,
        keywords=keywords,
    )
    assert hidden_claim.status == "needs_review"
    assert '"kubernetes"' in hidden_claim.warnings[0]


def test_factual_cover_letter_paragraph_without_evidence_is_unsupported() -> None:
    result = verdict("I containerised services.", [], section="cover_letter", factual=True)
    assert result == ClaimVerdict("unsupported", [NO_EVIDENCE_MESSAGE])


def test_connective_label_is_only_honoured_in_the_cover_letter() -> None:
    result = verdict("Thank you for reading.", [], section="summary", factual=False)
    assert result.status == "unsupported"


# ---- combining findings ------------------------------------------------------------


def test_strictest_finding_decides_and_every_warning_is_kept() -> None:
    result = verdict(
        "Expert Terraform user who cut cloud costs by 35%.", [COST_EVIDENCE, DOCKER_EVIDENCE]
    )
    assert result.status == "unsupported"
    assert len(result.warnings) == 3


# ---- whole documents ---------------------------------------------------------------


def claim(item_id: str, status: str) -> Claim:
    return Claim(item_id=item_id, text=item_id, validation_status=status)


def sample_documents() -> tuple[ResumeDocument, CoverLetter]:
    entry = ResumeEntry(
        entry_id="entry-1",
        record_id="role-1",
        category="employment",
        heading="Engineer",
        bullets=[claim("bullet", "needs_review")],
    )
    resume = ResumeDocument(
        contact=Contact(),
        summary=[claim("summary", "supported")],
        experience=[entry],
        skills=[claim("skill", "user_edited")],
    )
    letter = CoverLetter(
        paragraphs=[claim("greeting", "not_applicable"), claim("body", "unsupported")]
    )
    return resume, letter


def test_claims_are_listed_in_reading_order_with_their_section() -> None:
    located = list(iter_claims(*sample_documents()))
    assert [(item.section, item.claim.item_id, item.record_id) for item in located] == [
        ("summary", "summary", None),
        ("experience", "bullet", "role-1"),
        ("skills", "skill", None),
        ("cover_letter", "greeting", None),
        ("cover_letter", "body", None),
    ]
    assert list(iter_claims(None, None)) == []


def test_validation_summary_counts_statuses() -> None:
    resume, letter = sample_documents()
    summary = summarize_validation(resume, letter, None)
    assert summary.state == "needs_revalidation"
    assert (summary.needs_review_count, summary.unsupported_count, summary.user_edited_count) == (
        1,
        1,
        1,
    )
    resume.skills[0].validation_status = "supported"
    assert summarize_validation(resume, letter, None).state == "validated"


# ---- optional semantic verifier ----------------------------------------------------


class StubVerifier:
    """Answers verify_claims with fixed verdicts."""

    def __init__(self, *verdicts: tuple[str, str, str]) -> None:
        self.verdicts = verdicts
        self.calls = 0

    async def verify_claims(self, claims: list[LLMClaimCheck]) -> tuple[LLMVerification, Usage]:
        self.calls += 1
        results = [LLMVerdict(id=i, verdict=v, reason=r) for i, v, r in self.verdicts]
        return LLMVerification(results=results), Usage(provider_calls=1)


def check(item_id: str) -> LLMClaimCheck:
    return LLMClaimCheck(id=item_id, claim="claim", evidence_texts=["evidence"])


async def test_verifier_verdicts_only_ever_downgrade() -> None:
    verifier = StubVerifier(
        ("a", "supported", "fine"),
        ("b", "partially_supported", "adds a result"),
        ("c", "unsupported", "different tool"),
        ("not-asked", "unsupported", "invented id"),
    )
    findings, usage = await semantic_findings(verifier, [check("a"), check("b"), check("c")])

    assert usage.provider_calls == 1
    assert findings == {
        "b": Finding("needs_review", "Semantic check: adds a result"),
        "c": Finding("unsupported", "Semantic check: different tool"),
    }


async def test_verifier_is_not_called_without_claims() -> None:
    verifier = StubVerifier()
    assert await semantic_findings(verifier, []) == ({}, Usage())
    assert verifier.calls == 0


def test_stricter_never_improves_a_status() -> None:
    review = Finding("needs_review", "Semantic check: vague")
    reject = Finding("unsupported", "Semantic check: wrong")
    assert stricter(ClaimVerdict("supported", []), review) == ClaimVerdict(
        "needs_review", [review.message]
    )
    assert stricter(ClaimVerdict("needs_review", ["old"]), reject).status == "unsupported"
    # An unsupported statement stays unsupported whatever the verifier says.
    kept = stricter(ClaimVerdict("unsupported", ["old"]), review)
    assert kept == ClaimVerdict("unsupported", ["old", review.message])

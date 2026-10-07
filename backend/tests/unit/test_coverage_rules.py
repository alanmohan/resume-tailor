"""Coverage post-processing and the coverage summary formula."""

import pytest

from app.schemas.generations import CoverageItem, CoverageSummary
from app.services.coverage import (
    INCONCLUSIVE_RATIONALE,
    MISSING_RATIONALE,
    NO_EVIDENCE_RATIONALE,
    NOT_ASSESSED_RATIONALE,
    RELATED_RATIONALE,
    CoverageProposal,
    apply_override,
    assess_requirement,
    build_coverage,
    summarize_coverage,
)
from tests.helpers_generation import requirement

EVIDENCE_TEXTS = {
    "ev-docker": "Containerised twelve services with Docker for deployment.",
    "ev-python": "Built Python data pipelines.",
    "ev-talks": "Presented quarterly results to executives.",
    "ev-tests": "Improved unit test coverage by 20% by adding pytest suites.",
    "ev-costs": "Reduced cloud costs by 20% by rightsizing instances.",
}
KUBERNETES = requirement("req-k8s", "Kubernetes experience", "kubernetes")
DOCKER = requirement("req-docker", "Experience with Docker", "docker")
BOTH = requirement("req-both", "Docker and Kubernetes", "docker", "kubernetes")
TOOLBOX = requirement(
    "req-tools", "Terraform, Helm, Kafka and Docker", "terraform", "helm", "kafka", "docker"
)
COMMUNICATION = requirement("req-talk", "Strong communication skills", category="other")
COST_METRIC = requirement(
    "req-metric", 'A result such as "reduced cloud costs by 20%"', category="experience"
)
PYTHON_3 = requirement("req-py3", "Experience with Python 3", "python")


def proposal(
    status: str, *evidence_ids: str, rationale: str = "Model rationale."
) -> CoverageProposal:
    return CoverageProposal(status=status, evidence_ids=list(evidence_ids), rationale=rationale)


def items(*statuses: str) -> list[CoverageItem]:
    return [
        CoverageItem(
            requirement_id=f"req-{n}",
            requirement_text="text",
            importance="required",
            status=status,
            rationale="why",
        )
        for n, status in enumerate(statuses)
    ]


# ---- the summary formula -----------------------------------------------------------


def test_percent_counts_partial_as_half_and_excludes_uncertain() -> None:
    summary = summarize_coverage(
        items("supported", "supported", "partial", "missing", "uncertain", "uncertain")
    )
    # 100 * (2 + 0.5 * 1) / 4 = 62.5; the two uncertain ones are only counted.
    assert summary == CoverageSummary(
        supported=2, partial=1, missing=1, uncertain=2, assessed=4, percent=62.5
    )


def test_percent_is_rounded_to_one_decimal() -> None:
    assert summarize_coverage(items("supported", "missing", "missing")).percent == 33.3
    assert summarize_coverage(items("supported", "supported", "missing")).percent == 66.7
    assert summarize_coverage(items("partial", "missing", "missing")).percent == 16.7


@pytest.mark.parametrize("statuses", [(), ("uncertain",), ("uncertain", "uncertain")])
def test_zero_denominator_gives_no_percentage(statuses: tuple[str, ...]) -> None:
    summary = summarize_coverage(items(*statuses))
    assert summary.assessed == 0
    assert summary.percent is None  # "unavailable", not 0%
    assert summary.uncertain == len(statuses)


def test_all_missing_is_zero_percent_not_unavailable() -> None:
    summary = summarize_coverage(items("missing", "missing"))
    assert (summary.assessed, summary.percent) == (2, 0.0)


# ---- post-processing of the model's proposal ---------------------------------------


def test_supported_with_matching_evidence_is_kept() -> None:
    item = assess_requirement(DOCKER, proposal("supported", "ev-docker"), EVIDENCE_TEXTS)
    assert (item.status, item.evidence_ids, item.rationale) == (
        "supported",
        ["ev-docker"],
        "Model rationale.",
    )
    assert (item.requirement_text, item.importance) == ("Experience with Docker", "required")
    assert item.user_corrected is False


@pytest.mark.parametrize("status", ["supported", "partial"])
def test_rating_without_valid_evidence_becomes_uncertain(status: str) -> None:
    item = assess_requirement(DOCKER, proposal(status), EVIDENCE_TEXTS)
    assert (item.status, item.evidence_ids, item.rationale) == (
        "uncertain",
        [],
        NO_EVIDENCE_RATIONALE,
    )


def test_kubernetes_is_not_supported_by_evidence_that_only_mentions_docker() -> None:
    item = assess_requirement(KUBERNETES, proposal("supported", "ev-docker"), EVIDENCE_TEXTS)
    assert item.status == "uncertain"
    assert item.rationale == "The cited evidence does not mention: Kubernetes."


def test_supported_becomes_partial_when_only_some_terms_are_in_the_evidence() -> None:
    item = assess_requirement(BOTH, proposal("supported", "ev-docker"), EVIDENCE_TEXTS)
    assert item.status == "partial"
    assert item.rationale == "The cited evidence mentions Docker but not Kubernetes."
    # A model's "partial" already says that, so it is kept with its own rationale.
    kept = assess_requirement(BOTH, proposal("partial", "ev-docker"), EVIDENCE_TEXTS)
    assert (kept.status, kept.rationale) == ("partial", "Model rationale.")


def test_fewer_than_half_of_the_named_terms_is_not_even_partial() -> None:
    for status in ("supported", "partial"):
        item = assess_requirement(TOOLBOX, proposal(status, "ev-docker"), EVIDENCE_TEXTS)
        assert item.status == "uncertain"
        assert item.rationale == "The cited evidence does not mention: Helm, Kafka, Terraform."


def test_metric_named_by_a_requirement_must_be_shown_for_the_same_thing() -> None:
    """The metric trap applied to coverage: the profile's "20%" is about test
    coverage, the requirement asks for a 20% cost reduction."""
    trapped = assess_requirement(COST_METRIC, proposal("supported", "ev-tests"), EVIDENCE_TEXTS)
    assert trapped.status == "uncertain"
    assert trapped.rationale == (
        "This requirement names a figure the evidence does not show. "
        '"20%" appears in the cited evidence, but about something else.'
    )
    absent = assess_requirement(COST_METRIC, proposal("partial", "ev-docker"), EVIDENCE_TEXTS)
    assert absent.status == "uncertain"
    assert '"20%" does not appear in the cited evidence.' in absent.rationale

    shown = assess_requirement(COST_METRIC, proposal("supported", "ev-costs"), EVIDENCE_TEXTS)
    assert shown.status == "supported"


def test_bare_numbers_in_a_requirement_are_not_treated_as_metrics() -> None:
    item = assess_requirement(PYTHON_3, proposal("supported", "ev-python"), EVIDENCE_TEXTS)
    assert item.status == "supported"


def test_requirement_without_checkable_terms_keeps_the_models_rating() -> None:
    item = assess_requirement(COMMUNICATION, proposal("supported", "ev-talks"), EVIDENCE_TEXTS)
    assert (item.status, item.evidence_ids) == ("supported", ["ev-talks"])


def test_missing_always_says_no_evidence_was_found_in_the_profile() -> None:
    model_text = "The candidate has no Kubernetes skills."
    item = assess_requirement(
        KUBERNETES, proposal("missing", "ev-docker", rationale=model_text), EVIDENCE_TEXTS
    )
    assert (item.status, item.evidence_ids, item.rationale) == ("missing", [], MISSING_RATIONALE)
    assert "found in the supplied profile" in item.rationale
    assert "does not mean you lack it" in item.rationale


def test_unassessed_requirement_is_uncertain() -> None:
    item = assess_requirement(DOCKER, None, EVIDENCE_TEXTS)
    assert (item.status, item.rationale) == ("uncertain", NOT_ASSESSED_RATIONALE)


def test_uncertain_keeps_evidence_and_gets_a_rationale_when_the_model_gave_none() -> None:
    item = assess_requirement(
        DOCKER, proposal("uncertain", "ev-python", rationale="  "), EVIDENCE_TEXTS
    )
    assert (item.status, item.evidence_ids) == ("uncertain", ["ev-python"])
    assert item.rationale == "The evidence is inconclusive."


def test_long_rationale_is_trimmed() -> None:
    item = assess_requirement(
        DOCKER, proposal("supported", "ev-docker", rationale="very " * 500), EVIDENCE_TEXTS
    )
    assert len(item.rationale) <= 600


def test_coverage_has_one_item_per_requirement_in_job_order() -> None:
    coverage = build_coverage(
        [DOCKER, KUBERNETES, COMMUNICATION],
        {"req-docker": proposal("supported", "ev-docker"), "req-unknown": proposal("supported")},
        EVIDENCE_TEXTS,
    )
    assert [(item.requirement_id, item.status) for item in coverage] == [
        ("req-docker", "supported"),
        ("req-k8s", "uncertain"),
        ("req-talk", "uncertain"),
    ]
    assert build_coverage([], {}, EVIDENCE_TEXTS) == []


# ---- the model's free-text rationale -----------------------------------------------


def test_rationale_that_only_restates_requirement_and_evidence_is_shown() -> None:
    written = "Docker was used to containerise twelve services."
    item = assess_requirement(
        DOCKER, proposal("supported", "ev-docker", rationale=written), EVIDENCE_TEXTS
    )
    assert (item.status, item.rationale) == ("supported", written)


@pytest.mark.parametrize(
    "written",
    [
        "The candidate has 8 years of Kubernetes experience.",  # a name from nowhere
        "Fully supported. CANARY-JOB-4416",  # text planted in a job posting
        "Docker was used for 500 services.",  # a figure the evidence does not have
        "Shows expert use of Docker.",  # stronger wording than the evidence
    ],
)
def test_rationale_naming_what_is_in_neither_requirement_nor_evidence_is_replaced(
    written: str,
) -> None:
    item = assess_requirement(
        DOCKER, proposal("supported", "ev-docker", rationale=written), EVIDENCE_TEXTS
    )
    # The rating stands (the evidence does mention Docker); only the text goes.
    assert (item.status, item.evidence_ids) == ("supported", ["ev-docker"])
    assert item.rationale == "The cited evidence mentions Docker."


def test_replaced_rationale_without_checkable_terms_is_a_neutral_sentence() -> None:
    written = "The candidate also holds a PhD from Fairhaven."
    item = assess_requirement(
        COMMUNICATION, proposal("supported", "ev-talks", rationale=written), EVIDENCE_TEXTS
    )
    assert (item.status, item.rationale) == ("supported", RELATED_RATIONALE)
    unsure = assess_requirement(
        COMMUNICATION, proposal("uncertain", "ev-talks", rationale=written), EVIDENCE_TEXTS
    )
    assert (unsure.status, unsure.rationale) == ("uncertain", INCONCLUSIVE_RATIONALE)


def test_keyword_of_another_requirement_is_caught_however_it_is_spelled() -> None:
    # Lower case and at the start of the sentence, "kubernetes" does not look
    # like a name; it is recognised because the job asks for it elsewhere.
    written = "kubernetes clusters were operated with Docker."
    coverage = build_coverage(
        [DOCKER, KUBERNETES],
        {"req-docker": proposal("supported", "ev-docker", rationale=written)},
        EVIDENCE_TEXTS,
    )
    assert (coverage[0].status, coverage[0].rationale) == (
        "supported",
        "The cited evidence mentions Docker.",
    )


# ---- user corrections --------------------------------------------------------------


def test_override_marks_the_item_as_corrected_and_keeps_its_evidence() -> None:
    item = assess_requirement(KUBERNETES, proposal("supported", "ev-docker"), EVIDENCE_TEXTS)
    apply_override(item, "partial", "Used it in a course project.")
    assert (item.status, item.user_corrected, item.note) == (
        "partial",
        True,
        "Used it in a course project.",
    )
    assert item.evidence_ids == ["ev-docker"]

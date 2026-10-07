"""Demo mode: the fake provider matches and names whole keywords (review finding G-12).

Its rationales used to list word fragments ("github, actions but not ci, cd").
"""

from app.providers.base import LLMContextRequirement
from app.providers.fake.generation_ops import _coverage_item
from app.schemas.jobs import Requirement
from app.services.coverage import CoverageProposal, assess_requirement
from app.services.textutil import tokenize

EVIDENCE = {
    "E1": "Set up GitHub Actions pipelines that run tests and build Docker images.",
    "E2": "Kept the GitHub wiki current.",
    "E3": "Listed follow-up actions after each incident review.",
}
TOKENS = {alias: set(tokenize(text)) for alias, text in EVIDENCE.items()}
CI_TOOLS = "Experience with CI/CD tools such as GitHub Actions"


def need(text: str, *keywords: str, candidates: tuple[str, ...] = ()) -> LLMContextRequirement:
    return LLMContextRequirement(
        alias="R1",
        text=text,
        importance="required",
        category="skill",
        keywords=list(keywords),
        candidate_evidence=list(candidates),
    )


def test_rationale_names_whole_keywords() -> None:
    item = _coverage_item(need(CI_TOOLS, "ci/cd", "github actions"), TOKENS)
    assert (item.status, item.evidence) == ("partial", ["E1"])
    assert item.rationale == "The cited evidence mentions github actions but not ci/cd."


def test_several_keywords_read_as_a_sentence() -> None:
    supported = _coverage_item(
        need("Docker and GitHub Actions", "docker", "github actions"), TOKENS
    )
    assert supported.status == "supported"
    assert supported.rationale == "The cited evidence mentions docker and github actions."
    partial = _coverage_item(
        need("Docker, Helm, Kafka and CI", "docker", "helm", "kafka", "ci"), TOKENS
    )
    assert partial.rationale == "The cited evidence mentions docker but not helm, kafka and ci."


def test_keyword_needs_all_its_words_in_one_record() -> None:
    # "GitHub" in one record and "actions" in another are not "GitHub Actions".
    scattered = {alias: TOKENS[alias] for alias in ("E2", "E3")}
    item = _coverage_item(need(CI_TOOLS, "github actions"), scattered)
    assert (item.status, item.evidence) == ("missing", [])


def test_the_server_shows_the_fake_rationale_unchanged() -> None:
    proposed = _coverage_item(need(CI_TOOLS, "ci/cd", "github actions"), TOKENS)
    asked = Requirement(
        requirement_id="req-ci",
        text=CI_TOOLS,
        category="skill",
        importance="preferred",
        keywords=["ci/cd", "github actions"],
    )
    item = assess_requirement(
        asked, CoverageProposal(proposed.status, proposed.evidence, proposed.rationale), EVIDENCE
    )
    assert (item.status, item.rationale) == ("partial", proposed.rationale)

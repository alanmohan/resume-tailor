"""Requirement coverage: how well the retrieved evidence backs each job requirement.

The model proposes a status per requirement. The server does not take the
proposal at face value:

- "supported" or "partial" without valid evidence becomes "uncertain";
- when the requirement names a metric ("reduced costs by 20%") that the cited
  evidence does not show for the same thing, the status becomes "uncertain";
- when the requirement names something checkable (a technology, a
  certificate, a keyword) and the cited evidence mentions fewer than half of
  those terms, the status becomes "uncertain"; when it mentions at least half
  but not all, "supported" becomes "partial";
- "missing" always gets the same wording: no evidence was found in the
  supplied profile. That is a statement about the profile text, not about the
  person;
- the model's free-text rationale is shown only when every name, job keyword
  and figure in it comes from the requirement or from the evidence it cites.
  Otherwise a sentence written by the server takes its place, so text the
  model made up (or was told to write by an instruction planted in a posting)
  cannot reach the coverage panel.

The summary is evidence coverage, nothing more. It is not a suitability score,
an ATS score or a hiring probability.
"""

from dataclasses import dataclass

from app.schemas.generations import CoverageItem, CoverageStatus, CoverageSummary
from app.schemas.jobs import Requirement
from app.services.validation import (
    Vocabulary,
    escalation_findings,
    find_terms,
    number_findings,
    requirement_terms,
    singular_tokens,
)

MISSING_RATIONALE = (
    "No evidence for this requirement was found in the supplied profile. "
    "That does not mean you lack it: add it to your profile if you have it."
)
NOT_ASSESSED_RATIONALE = "The draft did not assess this requirement."
NO_EVIDENCE_RATIONALE = (
    "The draft rated this requirement without citing evidence from your profile, "
    "so the rating could not be confirmed."
)
INCONCLUSIVE_RATIONALE = "The evidence is inconclusive."
RELATED_RATIONALE = "The cited evidence relates to this requirement."
MAX_RATIONALE_CHARS = 600


@dataclass(frozen=True)
class CoverageProposal:
    """The model's rating of one requirement, after its evidence aliases were
    mapped back and every reference outside the retrieved context was dropped."""

    status: CoverageStatus
    evidence_ids: list[str]
    rationale: str


def _clean_rationale(rationale: str, fallback: str) -> str:
    text = " ".join(rationale.split())[:MAX_RATIONALE_CHARS]
    return text or fallback


def _listed(words: list[str]) -> str:
    return ", ".join(sorted(words, key=str.lower))


def _is_grounded(
    rationale: str, requirement: Requirement, cited_texts: list[str], job_terms: frozenset[str]
) -> bool:
    """Whether a rationale written by the model may be shown to the user.

    A rationale explains a rating by pointing at the requirement and at the
    cited evidence, so those are the only places its names, figures and
    expertise wording may come from. ``job_terms`` (the checkable terms of all
    the job's requirements) makes a posting keyword count as a name even where
    its spelling does not give it away, such as at the start of a sentence.
    """
    allowed_texts = [requirement.text, *cited_texts]
    vocabulary = Vocabulary.of(allowed_texts)
    return (
        all(vocabulary.has(term) for term in find_terms(rationale, job_terms))
        and not number_findings(rationale, allowed_texts)
        and not escalation_findings(rationale, allowed_texts)
    )


def _shown_rationale(
    proposal: CoverageProposal,
    requirement: Requirement,
    cited_texts: list[str],
    job_terms: frozenset[str],
    fallback: str,
) -> str:
    """The model's rationale when it is grounded, otherwise ``fallback``."""
    rationale = _clean_rationale(proposal.rationale, fallback)
    return rationale if _is_grounded(rationale, requirement, cited_texts, job_terms) else fallback


def _term_rationale(found: list[str], absent: list[str]) -> str:
    """Server-written rationale from the requirement's terms that the cited
    evidence does and does not mention."""
    if found and absent:
        return f"The cited evidence mentions {_listed(found)} but not {_listed(absent)}."
    if found:
        return f"The cited evidence mentions {_listed(found)}."
    return RELATED_RATIONALE


def _check_against_evidence(
    requirement: Requirement,
    proposal: CoverageProposal,
    cited_texts: list[str],
    job_terms: frozenset[str],
) -> tuple[CoverageStatus, str]:
    """Lower a "supported"/"partial" rating that the cited evidence does not
    bear out. Returns the status and rationale to use."""
    # A metric named by the requirement must be in the evidence, about the same
    # thing: "test coverage up 20%" does not show "cloud costs down 20%".
    figures = number_findings(requirement.text, cited_texts, only_with_unit=True)
    if figures:
        problems = " ".join(finding.message for finding in figures)
        return (
            "uncertain",
            f"This requirement names a figure the evidence does not show. {problems}",
        )

    terms = requirement_terms(requirement.text, requirement.keywords)
    cited_tokens = singular_tokens("\n".join(cited_texts))
    found = [word for key, word in terms.items() if key in cited_tokens]
    absent = [word for key, word in terms.items() if key not in cited_tokens]
    if absent and 2 * len(found) < len(terms):
        return "uncertain", f"The cited evidence does not mention: {_listed(absent)}."
    if absent and proposal.status == "supported":
        return "partial", _term_rationale(found, absent)
    return proposal.status, _shown_rationale(
        proposal, requirement, cited_texts, job_terms, _term_rationale(found, absent)
    )


def assess_requirement(
    requirement: Requirement,
    proposal: CoverageProposal | None,
    evidence_texts: dict[str, str],
    *,
    job_terms: frozenset[str] = frozenset(),
) -> CoverageItem:
    """Turn the model's proposal for one requirement into the stored coverage item.

    ``evidence_texts`` maps evidence_id to the text the validators search
    (the same text statements are validated against). ``job_terms`` holds the
    checkable terms of all the job's requirements (see _is_grounded).
    """
    cited_ids = proposal.evidence_ids if proposal else []
    cited_texts = [evidence_texts[key] for key in cited_ids if key in evidence_texts]
    if proposal is None:
        status, evidence_ids, rationale = "uncertain", [], NOT_ASSESSED_RATIONALE
    elif proposal.status == "missing":
        status, evidence_ids, rationale = "missing", [], MISSING_RATIONALE
    elif proposal.status == "uncertain":
        status, evidence_ids = "uncertain", proposal.evidence_ids
        rationale = _shown_rationale(
            proposal, requirement, cited_texts, job_terms, INCONCLUSIVE_RATIONALE
        )
    elif not proposal.evidence_ids:
        status, evidence_ids, rationale = "uncertain", [], NO_EVIDENCE_RATIONALE
    else:
        evidence_ids = proposal.evidence_ids
        status, rationale = _check_against_evidence(requirement, proposal, cited_texts, job_terms)
    return CoverageItem(
        requirement_id=requirement.requirement_id,
        requirement_text=requirement.text,
        importance=requirement.importance,
        status=status,
        evidence_ids=evidence_ids,
        rationale=rationale,
    )


def build_coverage(
    requirements: list[Requirement],
    proposals: dict[str, CoverageProposal],
    evidence_texts: dict[str, str],
) -> list[CoverageItem]:
    """One coverage item per requirement, in the job's own order. ``proposals``
    is keyed by requirement_id; ratings for unknown requirements never get here."""
    job_terms = frozenset(
        term
        for requirement in requirements
        for term in requirement_terms(requirement.text, requirement.keywords)
    )
    return [
        assess_requirement(
            requirement,
            proposals.get(requirement.requirement_id),
            evidence_texts,
            job_terms=job_terms,
        )
        for requirement in requirements
    ]


def summarize_coverage(items: list[CoverageItem]) -> CoverageSummary:
    """Counts per status and the coverage percentage.

    percent = 100 * (supported + 0.5 * partial) / assessed, rounded to one
    decimal, where assessed = supported + partial + missing. Uncertain
    requirements are left out of the percentage and reported as a count. With
    nothing assessed the percentage is None (shown as "unavailable"), never 0.
    """
    counts = {status: 0 for status in ("supported", "partial", "missing", "uncertain")}
    for item in items:
        counts[item.status] += 1
    assessed = counts["supported"] + counts["partial"] + counts["missing"]
    percent = None
    if assessed:
        percent = round(100 * (counts["supported"] + 0.5 * counts["partial"]) / assessed, 1)
    return CoverageSummary(**counts, assessed=assessed, percent=percent)


def apply_override(item: CoverageItem, status: CoverageStatus, note: str | None) -> None:
    """Record the user's own rating of a requirement. The evidence and the
    original rationale stay, so the correction remains visible as a correction."""
    item.status = status
    item.note = note
    item.user_corrected = True

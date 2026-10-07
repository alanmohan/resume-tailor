"""Models exchanged with the language model for generation and verification.

The model only ever sees short aliases: "E1".. for evidence, "P1".. for
confirmed profile records and "R1".. for requirements. Database IDs are never
sent; the server maps aliases back and drops any alias it did not issue.
"""

from typing import Literal

from app.providers.llm_model import LLMModel
from app.schemas.generations import CoverageStatus

Verdict = Literal["supported", "partially_supported", "unsupported"]
# "statement": something the candidate did or achieved (a bullet, a role
# summary, a skill list). "record_details": only the name, dates and place of
# the record itself. It shows that a role or degree exists, which can answer a
# requirement, but it is not material for a resume bullet.
EvidenceKind = Literal["statement", "record_details"]

# ---- Generation input ----------------------------------------------------------


class LLMJobBrief(LLMModel):
    title: str | None
    company: str | None
    role_summary: str | None


class LLMContextRequirement(LLMModel):
    alias: str
    text: str
    importance: str
    category: str
    keywords: list[str]
    # Aliases of the evidence retrieved for this requirement.
    candidate_evidence: list[str]


class LLMContextRecord(LLMModel):
    """Confirmed record metadata, shown so the model can group bullets under the
    right role. The server, not the model, writes these fields into the resume."""

    alias: str
    category: str
    title: str
    organization: str | None
    location: str | None
    start_date: str | None
    end_date: str | None


class LLMContextEvidence(LLMModel):
    alias: str
    # Alias of the profile record this evidence belongs to, if any.
    record: str | None
    category: str
    kind: EvidenceKind
    text: str


class LLMGenerationContext(LLMModel):
    job: LLMJobBrief
    requirements: list[LLMContextRequirement]
    records: list[LLMContextRecord]
    contact_name: str | None
    profile_skills: list[str]
    evidence: list[LLMContextEvidence]


# ---- Generation output ---------------------------------------------------------


class LLMStatement(LLMModel):
    """A summary line or cover-letter paragraph. ``factual`` is False only for
    connective text that makes no claim about the candidate."""

    text: str
    evidence: list[str]
    factual: bool


class LLMBulletOut(LLMModel):
    text: str
    evidence: list[str]


class LLMEntryOut(LLMModel):
    record: str
    bullets: list[LLMBulletOut]


class LLMSkillOut(LLMModel):
    name: str
    evidence: list[str]


class LLMCoverageOut(LLMModel):
    requirement: str
    status: CoverageStatus
    evidence: list[str]
    rationale: str


class LLMGeneration(LLMModel):
    summary: list[LLMStatement]
    experience: list[LLMEntryOut]
    projects: list[LLMEntryOut]
    skills: list[LLMSkillOut]
    cover_letter: list[LLMStatement]
    coverage: list[LLMCoverageOut]


# ---- Single-item regeneration --------------------------------------------------


class LLMRegenTarget(LLMModel):
    section: str
    current_text: str
    evidence: list[str]
    instruction: str | None
    # Concrete validation problems to fix, e.g. "20% is not in the cited evidence".
    feedback: list[str]


class LLMRegenResult(LLMModel):
    text: str
    evidence: list[str]
    factual: bool


# ---- Optional semantic verification --------------------------------------------


class LLMClaimCheck(LLMModel):
    id: str
    claim: str
    evidence_texts: list[str]


class LLMVerdict(LLMModel):
    id: str
    verdict: Verdict
    reason: str


class LLMVerification(LLMModel):
    results: list[LLMVerdict]

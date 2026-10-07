"""Models exchanged with the language model for profile extraction and job analysis.

The model refers to sources by alias ("S1", "S2", ...) and returns verbatim
quotes, never character offsets; the server locates each quote in the original
text (see app/services/textutil.py: locate_quote).
"""

from typing import Literal

from app.providers.llm_model import LLMModel
from app.schemas.jobs import Importance, RequirementCategory
from app.schemas.profiles import RecordCategory

ContactField = Literal["name", "email", "phone", "location", "link"]

# ---- Profile extraction --------------------------------------------------------


class LLMSource(LLMModel):
    """One labelled source handed to the model as delimited, untrusted data."""

    alias: str
    label: str
    source_type: str
    text: str


class LLMContactItem(LLMModel):
    field: ContactField
    value: str
    source: str
    quote: str


class LLMBullet(LLMModel):
    text: str
    source: str
    quote: str


class LLMRecord(LLMModel):
    category: RecordCategory
    title: str
    organization: str | None
    location: str | None
    start_date: str | None
    end_date: str | None
    summary: str | None
    source: str
    # Verbatim text of the line(s) naming the role, degree, project, ...
    header_quote: str
    bullets: list[LLMBullet]
    skills: list[str]
    ambiguous: bool
    ambiguity_notes: list[str]


class LLMConflictValue(LLMModel):
    value: str
    source: str
    quote: str


class LLMConflict(LLMModel):
    field: str
    description: str
    # Positions in LLMExtraction.records of the records that disagree.
    record_indexes: list[int]
    values: list[LLMConflictValue]


class LLMExtraction(LLMModel):
    contact: list[LLMContactItem]
    records: list[LLMRecord]
    conflicts: list[LLMConflict]


# ---- Job analysis --------------------------------------------------------------


class LLMJobInput(LLMModel):
    title: str | None
    company: str | None
    description: str


class LLMRequirement(LLMModel):
    text: str
    category: RequirementCategory
    importance: Importance
    inferred: bool
    # Verbatim supporting text from the job description; None when inferred.
    quote: str | None
    keywords: list[str]


class LLMJobAnalysis(LLMModel):
    role_summary: str
    requirements: list[LLMRequirement]

"""Building the model's context and composing a validated draft from its output.

Everything in this module is a pure function of its arguments: no database, no
provider, no clock. GenerationService (app/services/generation.py) calls it in
this order:

1. ``EvidenceIndex.of`` and ``build_grounding`` prepare what every statement is
   checked against.
2. ``build_model_context`` numbers the retrieved evidence (E1..), the confirmed
   records (P1..) and the requirements (R1..). The model only ever sees these
   aliases, never a database ID.
3. ``compose_draft`` maps the aliases in the model's answer back (unknown ones
   are dropped), validates every statement (app/services/validation.py) and
   composes all mandatory metadata (contact details, role and degree headers,
   dates, education, certifications) verbatim from the confirmed profile. The
   model cannot supply any of it.
4. ``collect_feedback`` lists the findings for the single correction pass, and
   ``remove_unsupported`` takes out what is still unsupported afterwards.
5. ``fill_empty_entries`` gives a confirmed role that ended without bullets its
   own confirmed bullets, so no role is printed as a bare heading.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from app.providers.base import (
    LLMContextEvidence,
    LLMContextRecord,
    LLMContextRequirement,
    LLMGeneration,
    LLMGenerationContext,
    LLMJobBrief,
)
from app.providers.generation_models import EvidenceKind
from app.schemas.common import new_id
from app.schemas.documents import EvidenceDoc, ProfileDoc
from app.schemas.evidence import EvidenceParent
from app.schemas.generations import (
    Claim,
    CoverLetter,
    OmittedClaim,
    ResumeDocument,
    ResumeEntry,
)
from app.schemas.jobs import Requirement
from app.schemas.profiles import ProfileRecord
from app.services.coverage import CoverageProposal
from app.services.indexing import statement_part
from app.services.textutil import LIMITED_EXPOSURE, fold_text, split_sentences, tokenize
from app.services.validation import (
    ClaimVerdict,
    GroundingContext,
    LocatedClaim,
    Section,
    Vocabulary,
    evidence_mentioning,
    find_terms,
    iter_claims,
    name_tokens,
    requirement_terms,
    singular_tokens,
    split_known_ids,
    validate_claim,
)

EXPERIENCE_CATEGORY = "employment"
PROJECT_CATEGORIES = frozenset({"project", "publication", "achievement"})
# Sections whose statements the model wrote and may rewrite on request.
REGENERABLE_SECTIONS: frozenset[Section] = frozenset(
    {"summary", "experience", "projects", "cover_letter"}
)
# Resume sections in which a bullet may only cite evidence of its own record.
RECORD_BOUND_SECTIONS: frozenset[Section] = frozenset({"experience", "projects"})
# Sections whose statements stand on their own, without a record's confirmed
# heading above them to say where the work was done or how well a skill is known.
FREE_STANDING_SECTIONS: frozenset[Section] = frozenset({"summary", "cover_letter"})

MAX_FEEDBACK_ITEMS = 20
MAX_FEEDBACK_TEXT_CHARS = 200
MAX_SKILL_CITATIONS = 2
# How many of its own confirmed bullets a role gets when it ended without any.
FALLBACK_BULLETS_PER_ROLE = 2
# Two bullets of one entry repeat each other when this share of the shorter
# one's content words also occurs in the other.
DUPLICATE_BULLET_OVERLAP = 0.75

OTHER_RECORD_MESSAGE = "Evidence from a different role or project was cited and not counted."
UNKNOWN_RECORD_REASON = "It was not listed under a confirmed role or project of your profile."
DUPLICATE_BULLET_MESSAGE = (
    "This bullet repeats most of another bullet of the same entry. Keep only one of them."
)
UNCITED_EDIT_MESSAGE = (
    "You edited this paragraph and it cites no evidence, so it could not be checked "
    "against your profile. Make sure everything it says about you is true."
)

# A statement in the first person ("I did not write down the dates") is a
# remark to the reader of the profile, not an achievement to print as a bullet.
_FIRST_PERSON = re.compile(r"\b(?:I|[Mm]y|[Mm]e)\b")
# The salutation that may open a cover letter, up to its comma.
_SALUTATION = re.compile(r"^\s*(?:dear|hello|hi)\b[^,.:;!?\n]*[,:]?\s*", re.IGNORECASE)
# How a sentence that only greets, thanks or signs off begins. Anything else in
# an uncited paragraph may be a statement about the applicant.
COURTESY_OPENERS = (
    "to whom it may concern",
    "thank you",
    "thanks",
    "sincerely",
    "best regards",
    "kind regards",
    "regards",
    "i am writing",
    "i am applying",
    "i am excited",
    "i am interested",
    "i would welcome",
    "i would be glad",
    "i would be happy",
    "i look forward",
    "i hope",
)


def is_limited_exposure(text: str) -> bool:
    """True when ``text`` says a skill is known from coursework or in passing only."""
    return LIMITED_EXPOSURE.search(text) is not None


def is_courtesy(text: str) -> bool:
    """True when ``text`` only greets, thanks or signs off: after an optional
    salutation ("Dear Hiring Manager,") every sentence begins with one of
    COURTESY_OPENERS.

    Used for a cover-letter paragraph the user edited that cites no evidence.
    Such a paragraph cannot be checked against the profile, so it may pass
    unreviewed only when it is recognisably a formula. The list is short on
    purpose: an unusual closing is sent to review, which costs one look, while
    an unchecked "I led the platform group" could put an invented
    qualification into the letter.
    """
    body = _SALUTATION.sub("", text, count=1)
    return all(
        fold_text(sentence.text).startswith(COURTESY_OPENERS) for sentence in split_sentences(body)
    )


# ---- Evidence lookup ---------------------------------------------------------------


def _searchable_text(evidence: EvidenceDoc) -> str:
    """Everything of an evidence record that may back a statement: the indexed
    statement, the original wording, its tags and the role/project it belongs to.

    Each part is a line of its own. The role or project is deliberately not on
    the statement's line (the indexed text carries it as a prefix, which is
    taken off here): the numeric check compares the words around a figure in a
    statement with the words next to that figure in the evidence, and a job
    title on that line would make "engineer who cut costs by 20%" look related
    to any figure of an engineering role.
    """
    parts = [statement_part(evidence.text), evidence.excerpt, ", ".join(evidence.tags)]
    if evidence.parent is not None:
        parts += [evidence.parent.title, evidence.parent.organization or ""]
    return "\n".join(part for part in parts if part)


def _is_limited_evidence(evidence: EvidenceDoc) -> bool:
    """True when the evidence describes what it mentions as coursework or
    passing exposure only: its own wording says so, or it is the list of a
    skill group whose title says so. The title of a role or project is no
    such qualifier: a bullet under "Teaching Assistant, Introductory
    Programming" is ordinary evidence."""
    parent = evidence.parent
    group_title = parent.title if parent is not None and parent.category == "skill" else ""
    return is_limited_exposure(
        "\n".join([statement_part(evidence.text), evidence.excerpt, group_title])
    )


@dataclass(frozen=True)
class EvidenceIndex:
    """All evidence of one profile version, with the forms the validators use.
    Every dictionary is keyed by evidence_id and keeps profile order."""

    documents: dict[str, EvidenceDoc]
    searchable: dict[str, str]
    tokens: dict[str, frozenset[str]]
    # IDs of the evidence that describes what it mentions as coursework or
    # passing exposure only (see _is_limited_evidence).
    limited: frozenset[str]

    @classmethod
    def of(cls, documents: Iterable[EvidenceDoc]) -> "EvidenceIndex":
        by_id = {document.evidence_id: document for document in documents}
        searchable = {key: _searchable_text(document) for key, document in by_id.items()}
        tokens = {key: singular_tokens(text) for key, text in searchable.items()}
        limited = frozenset(key for key, doc in by_id.items() if _is_limited_evidence(doc))
        return cls(documents=by_id, searchable=searchable, tokens=tokens, limited=limited)

    def texts(self, evidence_ids: Iterable[str]) -> list[str]:
        return [self.searchable[evidence_id] for evidence_id in evidence_ids]

    def home_record(self, evidence_id: str) -> str | None:
        """The role, project or other record an evidence record belongs to."""
        document = self.documents[evidence_id]
        return document.parent.record_id if document.parent else document.record_id

    def built_from(self, record_id: str, bullet_id: str | None) -> list[str]:
        """Evidence built from one confirmed bullet, or (``bullet_id`` None) from
        the record's own summary line."""
        return [
            key
            for key, document in self.documents.items()
            if document.record_id == record_id and document.bullet_id == bullet_id
        ]


def build_grounding(
    index: EvidenceIndex,
    requirements: list[Requirement],
    *,
    job_title: str | None,
    company: str | None,
    contact_name: str | None,
) -> GroundingContext:
    keywords: set[str] = set()
    for requirement in requirements:
        keywords.update(requirement_terms(requirement.text, requirement.keywords))
    return GroundingContext(
        profile=Vocabulary.of(index.searchable.values()),
        requirement_keywords=frozenset(keywords),
        letter_terms=name_tokens(job_title, company, contact_name),
        job_names=tuple(name.strip() for name in (job_title, company) if name and name.strip()),
    )


# ---- The model's view: aliases instead of database IDs -----------------------------


@dataclass(frozen=True)
class ModelContext:
    """What is sent to the model, plus the tables that turn the aliases in its
    answer back into database IDs. An alias missing from a table was not issued
    by the server and is ignored."""

    llm: LLMGenerationContext
    evidence_ids: dict[str, str]  # "E1" -> evidence_id
    record_ids: dict[str, str]  # "P1" -> record_id
    requirement_ids: dict[str, str]  # "R1" -> requirement_id

    def aliases_of(self, evidence_ids: list[str]) -> list[str]:
        alias_of = {evidence_id: alias for alias, evidence_id in self.evidence_ids.items()}
        return [alias_of[evidence_id] for evidence_id in evidence_ids if evidence_id in alias_of]


@dataclass(frozen=True)
class RecordBrief:
    """What the model is told about one confirmed role, project, degree or
    certification."""

    record_id: str
    category: str
    title: str
    organization: str | None
    location: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    # The record's summary paragraph, when the profile is at hand. It tells
    # summary evidence (a statement) apart from the record's header details.
    summary: str | None = None


def briefs_of_profile(records: list[ProfileRecord]) -> list[RecordBrief]:
    """Every confirmed record except skill groups, which hold no statements."""
    return [
        RecordBrief(
            record_id=record.record_id,
            category=record.category,
            title=record.title,
            organization=record.organization,
            location=record.location,
            start_date=record.start_date,
            end_date=record.end_date,
            summary=record.summary,
        )
        for record in records
        if record.category != "skill"
    ]


def briefs_of_evidence(evidence: list[EvidenceDoc]) -> list[RecordBrief]:
    """The roles and projects named by a draft's stored evidence. Used when a
    single statement is regenerated later: the draft keeps working from the
    evidence it was written from, even if the profile has changed since."""
    briefs: dict[str, RecordBrief] = {}
    for document in evidence:
        parent = document.parent
        if parent is not None and parent.record_id not in briefs:
            briefs[parent.record_id] = RecordBrief(
                record_id=parent.record_id,
                category=parent.category,
                title=parent.title,
                organization=parent.organization,
            )
    return list(briefs.values())


def model_text(evidence: EvidenceDoc) -> str:
    """The original wording on one line; the indexed text if there is none."""
    return " ".join(evidence.excerpt.split()) or evidence.text


def _evidence_kind(evidence: EvidenceDoc, record: RecordBrief | None) -> EvidenceKind:
    """Whether an evidence record states something the candidate did, or only
    restates the header of a role, project or degree (the indexer builds one
    such record per profile record, from its title line, dates and location).

    A bullet is always a statement. Evidence without a bullet is a statement
    when it belongs to no listed record (a skill list) or when its words come
    from the record's summary paragraph; otherwise it is the header record or
    the "Skills: ..." line of a role or project, which shows what the record
    lists but is not something the applicant achieved.
    """
    if evidence.bullet_id is not None or record is None:
        return "statement"
    summary_words = set(tokenize(record.summary or ""))
    excerpt_words = set(tokenize(evidence.excerpt))
    if excerpt_words and excerpt_words <= summary_words:
        return "statement"
    return "record_details"


def _plainly_listed(records: list[ProfileRecord]) -> Iterable[tuple[ProfileRecord, str]]:
    """``(record, skill)`` for every skill the confirmed profile lists as an
    ordinary skill. The skills of a group whose title is a qualifier
    ("Coursework exposure only") are left out: taken out of that group they
    would read as skills the applicant works with."""
    for record in records:
        if record.category == "skill" and is_limited_exposure(record.title):
            continue
        for skill in record.skills:
            yield record, skill


def profile_skills(records: list[ProfileRecord]) -> list[str]:
    """The skills offered to the model: every plainly listed skill of the
    confirmed profile, once, in profile order."""
    skills: dict[str, str] = {}
    for _, skill in _plainly_listed(records):
        skills.setdefault(skill.lower(), skill)
    return list(skills.values())


def listed_skills(records: list[ProfileRecord]) -> dict[str, str]:
    """``{skill name in comparison form: record_id}`` for every plainly listed
    skill, under the first record that lists it.

    The user confirmed these skills, so each may appear in a draft even when
    no evidence text happens to repeat its name (a skill listed under a role
    rather than in a skill group). "AWS (S3, EC2)" is also known as "AWS",
    unless the note in brackets is a qualifier such as "(coursework)".
    """
    listed: dict[str, str] = {}
    for record, skill in _plainly_listed(records):
        names = [skill] if is_limited_exposure(skill) else [skill, skill.partition("(")[0]]
        for name in names:
            if fold_text(name):
                listed.setdefault(fold_text(name), record.record_id)
    return listed


def build_model_context(
    job: LLMJobBrief,
    requirements: list[Requirement],
    records: list[RecordBrief],
    evidence: list[EvidenceDoc],
    candidates_by_requirement: dict[str, list[str]],
    index: EvidenceIndex,
    *,
    contact_name: str | None,
    skills: list[str],
) -> ModelContext:
    """Number the evidence (E1..), the records (P1..) and the requirements
    (R1..) and describe them to the model under those aliases."""
    evidence_alias = {doc.evidence_id: f"E{n}" for n, doc in enumerate(evidence, start=1)}
    record_alias = {record.record_id: f"P{n}" for n, record in enumerate(records, start=1)}
    record_of = {record.record_id: record for record in records}
    requirement_alias = {
        requirement.requirement_id: f"R{n}" for n, requirement in enumerate(requirements, start=1)
    }
    llm = LLMGenerationContext(
        job=job,
        requirements=[
            LLMContextRequirement(
                alias=requirement_alias[requirement.requirement_id],
                text=requirement.text,
                importance=requirement.importance,
                category=requirement.category,
                keywords=requirement.keywords,
                candidate_evidence=[
                    evidence_alias[evidence_id]
                    for evidence_id in candidates_by_requirement.get(requirement.requirement_id, [])
                    if evidence_id in evidence_alias
                ],
            )
            for requirement in requirements
        ],
        records=[
            LLMContextRecord(
                alias=record_alias[record.record_id],
                category=record.category,
                title=record.title,
                organization=record.organization,
                location=record.location,
                start_date=record.start_date,
                end_date=record.end_date,
            )
            for record in records
        ],
        contact_name=contact_name,
        profile_skills=skills,
        evidence=[
            LLMContextEvidence(
                alias=evidence_alias[doc.evidence_id],
                record=record_alias.get(index.home_record(doc.evidence_id) or ""),
                category=doc.category,
                kind=_evidence_kind(doc, record_of.get(index.home_record(doc.evidence_id) or "")),
                text=model_text(doc),
            )
            for doc in evidence
        ],
    )
    return ModelContext(
        llm=llm,
        evidence_ids={alias: evidence_id for evidence_id, alias in evidence_alias.items()},
        record_ids={alias: record_id for record_id, alias in record_alias.items()},
        requirement_ids={alias: key for key, alias in requirement_alias.items()},
    )


# ---- Validating statements against the index ---------------------------------------


def check_statement(
    text: str,
    evidence_ids: list[str],
    index: EvidenceIndex,
    grounding: GroundingContext,
    *,
    section: Section,
    record_id: str | None = None,
    factual: bool = True,
) -> tuple[list[str], ClaimVerdict]:
    """Validate one statement. Returns the evidence IDs that count for it and
    the verdict.

    IDs that are not evidence of this profile version are dropped. For a
    bullet under a role or project, evidence of any other record is dropped
    too: a bullet under an employer must be backed by what happened at that
    employer, not by a personal project or a skills list.
    """
    usable = [evidence_id for evidence_id in evidence_ids if evidence_id in index.documents]
    foreign = 0
    if section in RECORD_BOUND_SECTIONS and record_id is not None:
        own = [evidence_id for evidence_id in usable if index.home_record(evidence_id) == record_id]
        foreign = len(usable) - len(own)
        usable = own
    verdict = validate_claim(text, index.texts(usable), grounding, section=section, factual=factual)
    if foreign and verdict.status != "supported":
        verdict = ClaimVerdict(verdict.status, [OTHER_RECORD_MESSAGE, *verdict.warnings])
    if section in FREE_STANDING_SECTIONS and verdict.status == "supported":
        doubts = [
            *_limited_exposure_doubts(text, usable, index, grounding),
            *_borrowed_evidence_doubts(text, usable, index, grounding, section),
        ]
        if doubts:
            verdict = ClaimVerdict("needs_review", doubts)
    return usable, verdict


def _limited_exposure_message(name: str) -> str:
    return f'Your profile mentions "{name}" only as coursework or limited exposure.'


def _limited_exposure_doubts(
    text: str, cited: list[str], index: EvidenceIndex, grounding: GroundingContext
) -> list[str]:
    """Warnings for names and job keywords that the confirmed profile mentions
    only as coursework or passing exposure, in a statement that drops that
    qualifier.

    The cited evidence does contain the word, so the ordinary checks pass;
    what is lost is "coursework only". "Experienced with TensorFlow" must not
    be backed by "Coursework exposure only: TensorFlow".
    """
    cited_limited = [evidence_id for evidence_id in cited if evidence_id in index.limited]
    if not cited_limited or is_limited_exposure(text):
        return []
    limited = Vocabulary.of(index.texts(cited_limited))
    plain = Vocabulary.of(
        text for evidence_id, text in index.searchable.items() if evidence_id not in index.limited
    )
    return [
        _limited_exposure_message(term.raw)
        for term in find_terms(text, grounding.requirement_keywords)
        if limited.has(term) and not plain.has(term)
    ]


def _names_record(sentence: str, parent: EvidenceParent) -> bool:
    """True when the sentence names the record: an employer by its
    organisation, a project by its title (whole words, any letter case)."""
    name = parent.organization if parent.category == EXPERIENCE_CATEGORY else parent.title
    if not name or not name.strip():
        return False
    pattern = rf"(?<!\w){re.escape(fold_text(name))}(?!\w)"
    return re.search(pattern, fold_text(sentence)) is not None


def _borrowed_evidence_doubts(
    text: str, cited: list[str], index: EvidenceIndex, grounding: GroundingContext, section: Section
) -> list[str]:
    """Warnings for sentences that name an employer but rest on what was done
    in another role or in a project.

    A summary line or cover-letter paragraph may cite several records, and the
    ordinary checks accept a word or figure found in any of them. So "At
    Brightloom Labs I built search over 12,000 trip reports" passes when it
    cites one Brightloom bullet and the personal project the figure comes from.

    Rule: a sentence that names an employer is checked again without the cited
    evidence of every role and project it does not name. If it no longer
    holds, a project or another job is being presented as work at that employer.
    """
    parents = {evidence_id: index.documents[evidence_id].parent for evidence_id in cited}
    doubts: list[str] = []
    for sentence in split_sentences(text):
        named = {
            evidence_id
            for evidence_id, parent in parents.items()
            if parent is not None and _names_record(sentence.text, parent)
        }
        employers = [
            parents[evidence_id].organization
            for evidence_id in cited
            if evidence_id in named and parents[evidence_id].category == EXPERIENCE_CATEGORY
        ]
        own = [
            evidence_id
            for evidence_id, parent in parents.items()
            if evidence_id in named
            or parent is None
            or parent.category not in (EXPERIENCE_CATEGORY, *PROJECT_CATEGORIES)
        ]
        if not employers or len(own) == len(parents):
            continue
        again = validate_claim(sentence.text, index.texts(own), grounding, section=section)
        message = (
            f"This sentence names {employers[0]} but relies on evidence from another "
            "role or project."
        )
        if again.status != "supported" and message not in doubts:
            doubts.append(message)
    return doubts


def check_skill(
    name: str,
    preferred_ids: list[str],
    index: EvidenceIndex,
    listed: Mapping[str, str] | None = None,
) -> tuple[list[str], ClaimVerdict]:
    """Decide whether a skill may be listed. Returns the citations and the
    verdict; the server picks the citations itself (evidence the model cited
    is tried first).

    - Evidence names the skill without a qualifier: supported.
    - No evidence names it, but the confirmed profile lists it (``listed``,
      see listed_skills): supported, citing the record that lists it. The user
      confirmed the skill; it is simply written under a role or project.
    - Evidence names it only as coursework or passing exposure: needs review,
      unless the skill's own text keeps that qualifier.
    - Otherwise it is not in the confirmed profile: unsupported.
    """
    mentioning = evidence_mentioning(name, index.tokens, preferred_ids, limit=len(index.tokens))
    plain = [evidence_id for evidence_id in mentioning if evidence_id not in index.limited]
    if plain or (mentioning and is_limited_exposure(name)):
        return (plain or mentioning)[:MAX_SKILL_CITATIONS], ClaimVerdict("supported", [])
    listing_record = (listed or {}).get(fold_text(name))
    if listing_record is not None:
        return index.built_from(listing_record, None)[:1], ClaimVerdict("supported", [])
    if mentioning:
        return mentioning[:MAX_SKILL_CITATIONS], ClaimVerdict(
            "needs_review", [_limited_exposure_message(name)]
        )
    message = f'"{name}" is not mentioned in your confirmed profile.'
    return [], ClaimVerdict("unsupported", [message])


class ClaimBuilder:
    """Turns statements written by the model into validated claims and counts
    the citations that had to be ignored."""

    def __init__(
        self, context: ModelContext, index: EvidenceIndex, grounding: GroundingContext
    ) -> None:
        self._context = context
        self._index = index
        self._grounding = grounding
        self.ignored_citations = 0

    def resolve(self, aliases: list[str]) -> list[str]:
        """Evidence IDs for the aliases the model cited. Anything that is not
        an alias issued for this draft is counted and dropped."""
        known, unknown = split_known_ids(aliases, self._context.evidence_ids)
        self.ignored_citations += len(unknown)
        return [self._context.evidence_ids[alias] for alias in known]

    def build(
        self,
        text: str,
        aliases: list[str],
        *,
        section: Section,
        record_id: str | None = None,
        factual: bool = True,
    ) -> Claim:
        evidence_ids, verdict = check_statement(
            text,
            self.resolve(aliases),
            self._index,
            self._grounding,
            section=section,
            record_id=record_id,
            factual=factual,
        )
        return Claim(
            item_id=new_id(),
            text=text,
            evidence_ids=evidence_ids,
            validation_status=verdict.status,
            warnings=verdict.warnings,
        )


# ---- Composing a draft from the model's output -------------------------------------


@dataclass
class Draft:
    """A composed, validated draft before unsupported statements are removed."""

    resume: ResumeDocument
    cover_letter: CoverLetter
    coverage: dict[str, CoverageProposal]  # keyed by requirement_id
    # Bullets the model placed under a record the server did not issue.
    misplaced: list[OmittedClaim]
    ignored_citations: int

    def claims(self) -> list[LocatedClaim]:
        return list(iter_claims(self.resume, self.cover_letter))

    def unsupported_count(self) -> int:
        unsupported = [
            located for located in self.claims() if located.claim.validation_status == "unsupported"
        ]
        return len(unsupported) + len(self.misplaced)

    def needs_correction_pass(self) -> bool:
        """True when a second model call could repair something: an unsupported
        sentence or bullet can be rewritten from the evidence. An unsupported
        skill name cannot be reworded, only dropped, and dropping it needs no
        model call, so skills alone never pay for a correction pass."""
        return bool(self.misplaced) or any(
            located.claim.validation_status == "unsupported" and located.section != "skills"
            for located in self.claims()
        )


def _date_range(record: ProfileRecord) -> str | None:
    """The record's dates exactly as confirmed, joined with a hyphen."""
    dates = [date for date in (record.start_date, record.end_date) if date]
    return " - ".join(dates) or None


def _header_entry(record: ProfileRecord, bullets: list[Claim]) -> ResumeEntry:
    """A resume entry whose header is copied from the confirmed record."""
    return ResumeEntry(
        entry_id=new_id(),
        record_id=record.record_id,
        category=record.category,
        heading=record.title,
        subheading=record.organization,
        location=record.location,
        date_range=_date_range(record),
        bullets=bullets,
    )


def _confirmed_entry(record: ProfileRecord, index: EvidenceIndex) -> ResumeEntry:
    """An education or certification entry: header and statements are the
    confirmed profile text, unchanged, each citing the evidence built from it."""
    statements = [(record.summary, None)] if record.summary else []
    statements += [(bullet.text, bullet.bullet_id) for bullet in record.bullets]
    bullets = [
        Claim(
            item_id=new_id(),
            text=text,
            evidence_ids=index.built_from(record.record_id, bullet_id),
            validation_status="supported",
        )
        for text, bullet_id in statements
    ]
    return _header_entry(record, bullets)


def _bullet_section(record: ProfileRecord | None) -> Section | None:
    """The resume section a record's bullets belong in. It follows from the
    confirmed category, never from where the model put the entry, so a project
    cannot be presented as employment."""
    if record is None:
        return None
    if record.category == EXPERIENCE_CATEGORY:
        return "experience"
    if record.category in PROJECT_CATEGORIES:
        return "projects"
    return None


def _compose_entries(
    output: LLMGeneration,
    records: dict[str, ProfileRecord],
    context: ModelContext,
    builder: ClaimBuilder,
) -> tuple[dict[str, list[Claim]], list[OmittedClaim]]:
    """Validated bullets per record_id, in the order the model listed the
    records. A record listed without bullets is kept with an empty list: that
    is how a project or publication is shown by its confirmed header alone.

    The second result holds bullets written under an alias the server did not
    issue, or under a record that cannot hold bullets (a degree, a certificate).
    """
    bullets_by_record: dict[str, list[Claim]] = {}
    misplaced: list[OmittedClaim] = []
    for model_section, entries in (
        ("experience", output.experience),
        ("projects", output.projects),
    ):
        for entry in entries:
            record = records.get(context.record_ids.get(entry.record, ""))
            section = _bullet_section(record)
            if record is None or section is None:
                misplaced += [
                    OmittedClaim(
                        section=model_section,
                        text=bullet.text.strip(),
                        reason=UNKNOWN_RECORD_REASON,
                    )
                    for bullet in entry.bullets
                    if bullet.text.strip()
                ]
                continue
            bullets = bullets_by_record.setdefault(record.record_id, [])
            bullets += [
                builder.build(
                    bullet.text.strip(),
                    bullet.evidence,
                    section=section,
                    record_id=record.record_id,
                )
                for bullet in entry.bullets
                if bullet.text.strip()
            ]
    for bullets in bullets_by_record.values():
        flag_repeated_bullets(bullets)
    return bullets_by_record, misplaced


def flag_repeated_bullets(bullets: list[Claim]) -> None:
    """Mark a supported bullet for review when it repeats an earlier bullet of
    the same entry.

    A profile can state one fact twice (in a role's summary paragraph and
    again as an outcome bullet), and the model then writes a bullet from each.
    Two bullets repeat each other when at least DUPLICATE_BULLET_OVERLAP of
    the shorter one's words occur in the other. The earlier bullet is left
    alone and nothing is deleted: the user decides which wording to keep.
    """
    seen: list[frozenset[str]] = []
    for bullet in bullets:
        words = singular_tokens(bullet.text)
        repeats = any(
            words
            and earlier
            and len(words & earlier) / min(len(words), len(earlier)) >= DUPLICATE_BULLET_OVERLAP
            for earlier in seen
        )
        if repeats and bullet.validation_status == "supported":
            bullet.validation_status = "needs_review"
            bullet.warnings = [DUPLICATE_BULLET_MESSAGE]
        seen.append(words)


def _compose_skills(
    output: LLMGeneration, builder: ClaimBuilder, index: EvidenceIndex, listed: Mapping[str, str]
) -> list[Claim]:
    """One claim per distinct skill name (case-insensitive)."""
    skills: dict[str, Claim] = {}
    for skill in output.skills:
        name = " ".join(skill.name.split())
        if not name or name.lower() in skills:
            continue
        evidence_ids, verdict = check_skill(name, builder.resolve(skill.evidence), index, listed)
        skills[name.lower()] = Claim(
            item_id=new_id(),
            text=name,
            evidence_ids=evidence_ids,
            validation_status=verdict.status,
            warnings=verdict.warnings,
        )
    return list(skills.values())


def _compose_coverage(
    output: LLMGeneration, context: ModelContext, builder: ClaimBuilder
) -> dict[str, CoverageProposal]:
    """The model's rating per requirement_id. Ratings for unknown requirement
    aliases are dropped; of several ratings for one requirement the first counts."""
    proposals: dict[str, CoverageProposal] = {}
    for item in output.coverage:
        requirement_id = context.requirement_ids.get(item.requirement)
        if requirement_id is not None and requirement_id not in proposals:
            proposals[requirement_id] = CoverageProposal(
                status=item.status,
                evidence_ids=builder.resolve(item.evidence),
                rationale=item.rationale,
            )
    return proposals


def compose_draft(
    output: LLMGeneration,
    profile: ProfileDoc,
    context: ModelContext,
    index: EvidenceIndex,
    grounding: GroundingContext,
) -> Draft:
    """Build the resume, cover letter and coverage proposals from the model's
    output. Contact details and every header come from ``profile``."""
    builder = ClaimBuilder(context, index, grounding)
    records = {record.record_id: record for record in profile.records}
    bullets_by_record, misplaced = _compose_entries(output, records, context, builder)

    resume = ResumeDocument(
        contact=profile.contact,
        summary=[
            builder.build(statement.text.strip(), statement.evidence, section="summary")
            for statement in output.summary
            if statement.text.strip()
        ],
        # Every confirmed role is listed, in profile order, even without bullets:
        # the employment history is mandatory metadata, not a retrieval result.
        experience=[
            _header_entry(record, bullets_by_record.get(record.record_id, []))
            for record in profile.records
            if record.category == EXPERIENCE_CATEGORY
        ],
        # Projects, publications and achievements appear when the model listed
        # them, in its order: they are optional and chosen for the job.
        projects=[
            _header_entry(records[record_id], bullets)
            for record_id, bullets in bullets_by_record.items()
            if records[record_id].category in PROJECT_CATEGORIES
        ],
        education=[
            _confirmed_entry(record, index)
            for record in profile.records
            if record.category == "education"
        ],
        certifications=[
            _confirmed_entry(record, index)
            for record in profile.records
            if record.category == "certification"
        ],
        skills=_compose_skills(output, builder, index, listed_skills(profile.records)),
    )
    cover_letter = CoverLetter(
        paragraphs=[
            builder.build(
                paragraph.text.strip(),
                paragraph.evidence,
                section="cover_letter",
                factual=paragraph.factual,
            )
            for paragraph in output.cover_letter
            if paragraph.text.strip()
        ]
    )
    return Draft(
        resume=resume,
        cover_letter=cover_letter,
        coverage=_compose_coverage(output, context, builder),
        misplaced=misplaced,
        ignored_citations=builder.ignored_citations,
    )


def collect_feedback(draft: Draft) -> list[str]:
    """Concrete findings for the correction pass, one line per flagged statement."""
    feedback = [
        f'{located.section}: "{located.claim.text[:MAX_FEEDBACK_TEXT_CHARS]}" - '
        + " ".join(located.claim.warnings)
        for located in draft.claims()
        if located.claim.validation_status in ("unsupported", "needs_review")
    ]
    feedback += [
        f'{omitted.section}: "{omitted.text[:MAX_FEEDBACK_TEXT_CHARS]}" - {omitted.reason}'
        for omitted in draft.misplaced
    ]
    return feedback[:MAX_FEEDBACK_ITEMS]


def remove_unsupported(draft: Draft) -> list[OmittedClaim]:
    """Take every unsupported statement out of the draft and return the list of
    what was removed and why. An entry that loses all its bullets keeps its
    header: the header is confirmed profile data, not a claim of the model."""
    omitted = list(draft.misplaced)

    def keep_supported(section: Section, claims: list[Claim]) -> list[Claim]:
        kept = []
        for claim in claims:
            if claim.validation_status == "unsupported":
                reason = " ".join(claim.warnings)
                omitted.append(OmittedClaim(section=section, text=claim.text, reason=reason))
            else:
                kept.append(claim)
        return kept

    resume = draft.resume
    resume.summary = keep_supported("summary", resume.summary)
    for entry in resume.experience:
        entry.bullets = keep_supported("experience", entry.bullets)
    for entry in resume.projects:
        entry.bullets = keep_supported("projects", entry.bullets)
    resume.skills = keep_supported("skills", resume.skills)
    draft.cover_letter.paragraphs = keep_supported("cover_letter", draft.cover_letter.paragraphs)
    return omitted


def _own_statements(record: ProfileRecord) -> list[tuple[str, str | None]]:
    """``(text, bullet_id)`` of what a confirmed record says was done: its
    bullets, or its summary paragraph (bullet_id None) when it has no bullets.
    Statements in the first person are left out; they are remarks ("I did not
    write down the dates"), not achievements."""
    statements: list[tuple[str, str | None]] = [
        (bullet.text, bullet.bullet_id) for bullet in record.bullets
    ]
    if not statements and record.summary:
        statements = [(record.summary, None)]
    return [(text, bullet_id) for text, bullet_id in statements if not _FIRST_PERSON.search(text)]


def fill_empty_entries(draft: Draft, profile: ProfileDoc, index: EvidenceIndex) -> list[str]:
    """Make sure no entry of the resume is printed as a bare heading. Call it
    after ``remove_unsupported``. Returns the evidence IDs the added bullets cite.

    Why this is needed: a bullet may only cite evidence of its own role, and
    retrieval selects evidence by relevance to the job. A confirmed role whose
    statements were not retrieved, or whose bullets were all removed, would
    otherwise appear with a heading and nothing under it.

    - A role without bullets gets up to FALLBACK_BULLETS_PER_ROLE of its own
      confirmed statements, word for word, each citing the evidence built from
      it. They are "supported" without a check because the text is the
      confirmed evidence itself. A role whose record has no usable statement
      keeps its heading: the employment history is confirmed either way.
    - A project, publication or achievement without bullets is dropped; it is
      optional. It stays only when its confirmed record has no statements at
      all, because then the title is all there is to show.
    """
    records = {record.record_id: record for record in profile.records}
    cited: list[str] = []
    for entry in draft.resume.experience:
        if entry.bullets:
            continue
        for text, bullet_id in _own_statements(records[entry.record_id]):
            evidence_ids = index.built_from(entry.record_id, bullet_id)
            if not evidence_ids:
                continue
            entry.bullets.append(
                Claim(
                    item_id=new_id(),
                    text=text,
                    evidence_ids=evidence_ids,
                    validation_status="supported",
                )
            )
            cited += evidence_ids
            if len(entry.bullets) == FALLBACK_BULLETS_PER_ROLE:
                break
    draft.resume.projects = [
        entry
        for entry in draft.resume.projects
        if entry.bullets
        or not (records[entry.record_id].bullets or records[entry.record_id].summary)
    ]
    return cited

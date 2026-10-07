"""Deterministic grounding checks for generated and user-edited statements.

Every statement in a draft (summary line, bullet, skill, cover-letter
paragraph) ends with one validation status and human-readable warnings:

    supported       every check passed against the evidence the statement cites
    needs_review    something could not be confirmed; the user must look at it
    unsupported     a concrete problem was found; generation removes the statement
    not_applicable  connective cover-letter text that makes no claim

The checks are plain string and set comparisons, so they are repeatable and
cheap, and they are deliberately conservative. They reduce the risk of
fabricated claims but cannot eliminate it: a sentence can reuse the right
words and still say something the evidence does not. The optional semantic
verifier at the end of this module is a second opinion that can only make a
status stricter.

Nothing here touches the database, so everything is unit tested directly
(tests/unit/test_validation_*.py).
"""

import math
import re
from collections.abc import Collection, Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.providers.base import AIProvider, LLMClaimCheck, Usage
from app.schemas.generations import (
    Claim,
    CoverLetter,
    ResumeDocument,
    ValidationStatus,
    ValidationSummary,
)
from app.services.textutil import STOPWORDS, split_sentences, tokenize

Severity = Literal["needs_review", "unsupported"]
Section = Literal[
    "summary", "experience", "projects", "education", "certifications", "skills", "cover_letter"
]

NO_EVIDENCE_MESSAGE = "No supporting evidence from your profile is cited for this statement."


@dataclass(frozen=True)
class Finding:
    """One problem found in a statement."""

    severity: Severity
    message: str


@dataclass(frozen=True)
class ClaimVerdict:
    status: ValidationStatus
    warnings: list[str]


# ---- (a) Evidence-ID membership ----------------------------------------------------


def split_known_ids(cited: Iterable[str], known: Collection[str]) -> tuple[list[str], list[str]]:
    """Separate cited references into those issued for this draft and the rest.

    The model may only cite evidence that was retrieved for it. Anything else
    (an invented alias, a database ID, another session's ID) lands in the
    second list and is never looked up. Order is kept, duplicates are dropped.
    """
    accepted: list[str] = []
    rejected: list[str] = []
    for reference in cited:
        target = accepted if reference in known else rejected
        if reference not in target:
            target.append(reference)
    return accepted, rejected


# ---- Words, terms and vocabularies -------------------------------------------------

# Same token shape as textutil.tokenize, so a term found here compares equal to
# a token of the evidence. Kept separately because the original spelling
# (capital letters) and the position are needed too.
_WORD_PATTERN = re.compile(r"(?:[^\W_]|[+#.])+")
# Characters that may stand between the end of a sentence and its first word.
_SENTENCE_LEAD_IN = frozenset(" \t\"'“”‘’([•*-")
_SENTENCE_END = frozenset(".!?:;\n")
# "20", "5,000", "3x", "5k", "10+", "2nd", "1990s": quantities and ordinals, not names.
_NUMBER_LIKE = re.compile(r"\d[\d.,]*(?:st|nd|rd|th|s|x|k|m|mm|b|bn)?\+?")


def _singular(token: str) -> str:
    """Drop a plural "s" so "pipelines" equals "pipeline". Both sides of every
    comparison go through this, so odd results ("kubernete") still match."""
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _stem(token: str) -> str:
    """Crude stem for comparing ordinary words: strip a common ending and keep
    five letters, so "reduced", "reducing" and "reduction" all become "reduc".
    Too loose for names ("react" / "reactive"), which use _singular instead."""
    for suffix in ("ing", "ed", "es", "s"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            token = token[: -len(suffix)]
            break
    return token[:5]


# Endings a technology name takes when it is used as a verb or adjective.
_NAME_ENDINGS = ("ized", "ised", "izing", "ising", "ize", "ise", "ed", "ing")

# Words found in almost every job posting. They say nothing checkable about a
# candidate, so they are never treated as requirement keywords.
GENERIC_JOB_WORDS = frozenset(
    _singular(word)
    for word in (
        "ability able background candidate demonstrated equivalent excellent experience "
        "experienced familiar familiarity good great including knowledge minimum plus "
        "preferred proven related relevant required requirements role skills solid strong "
        "team understanding using work working years"
    ).split()
)

# Words of a salutation or sign-off that are capitalised by convention.
LETTER_FORMALITIES = frozenset(
    _singular(word)
    for word in (
        "dear hiring manager recruiter team committee sir madam sincerely regards best"
    ).split()
)


@dataclass(frozen=True)
class Vocabulary:
    """The words of some text in the two forms that terms are compared in."""

    # Singular tokens: a name such as "kubernetes" must really be there.
    exact: frozenset[str]
    # Crude stems: "tested" is enough to support the ordinary word "testing".
    stems: frozenset[str]

    @classmethod
    def of(cls, texts: Iterable[str]) -> "Vocabulary":
        tokens = {token for text in texts for token in tokenize(text)}
        return cls(
            exact=frozenset(_singular(token) for token in tokens),
            stems=frozenset(_stem(token) for token in tokens),
        )

    def has(self, term: "Term") -> bool:
        if term.key in self.exact:
            return True
        if not term.name_shaped:
            return _stem(term.token) in self.stems
        # A name may carry a verb ending: "Dockerized" is backed by "Docker".
        # The rest of the word must be exactly a name in the text, so
        # "Cloudflare" is not backed by "cloud".
        return any(
            term.key.endswith(ending) and term.key.removesuffix(ending) in self.exact
            for ending in _NAME_ENDINGS
        )


@dataclass(frozen=True)
class Term:
    """A word of a statement that must be backed by evidence."""

    raw: str  # as written, for messages
    token: str  # lower-cased
    key: str  # singular form used for exact comparison
    # True for technology names and proper nouns, False for an ordinary word
    # that is only checked because the job posting uses it as a keyword.
    name_shaped: bool


def _starts_sentence(text: str, start: int) -> bool:
    """True when only spaces, quotes or a bullet mark separate the word at
    ``start`` from the beginning of the text or the end of the previous sentence."""
    index = start - 1
    while index >= 0 and text[index] in _SENTENCE_LEAD_IN:
        index -= 1
    return index < 0 or text[index] in _SENTENCE_END


def _has_name_shape(word: str) -> bool:
    """Spelling that marks a name wherever it stands: an inner capital
    ("PostgreSQL", "AWS"), a digit ("s3", "p95") or a symbol that belongs to
    technology names ("c++", "c#", "node.js")."""
    return (
        any(char.isupper() for char in word[1:])
        or any(char.isdigit() for char in word)
        or any(char in "+#." for char in word)
    )


def _candidate_words(text: str) -> Iterator[tuple[int, str, str]]:
    """``(position, word as written, lower-cased token)`` for every word that
    could be a term. Stopwords, single letters and quantities never are."""
    for match in _WORD_PATTERN.finditer(text):
        word = match.group().strip(".")
        token = word.lower()
        if len(token) >= 2 and token not in STOPWORDS and not _NUMBER_LIKE.fullmatch(token):
            yield match.start(), word, token


def find_terms(text: str, requirement_keywords: frozenset[str]) -> list[Term]:
    """The words of ``text`` that need evidence, each listed once.

    A word is a term when it is

    - name shaped: written with a capital letter that is not explained by
      starting a sentence ("... using Kubernetes"), or spelled like a
      technology anywhere ("PostgreSQL", "CI", "c++"); or
    - a keyword of the job's requirements, however it is written. Copying the
      posting's vocabulary into the draft is how invented qualifications
      usually appear.

    Limit: a plain capitalised word that starts a sentence ("Kubernetes
    clusters ...") is only caught when it is also a requirement keyword,
    because it cannot be told apart from an ordinary first word ("Built ...").
    """
    terms: dict[str, Term] = {}
    for position, word, token in _candidate_words(text):
        key = _singular(token)
        capitalised_mid_sentence = word[0].isupper() and not _starts_sentence(text, position)
        name_shaped = capitalised_mid_sentence or _has_name_shape(word)
        if name_shaped or key in requirement_keywords:
            terms.setdefault(key, Term(raw=word, token=token, key=key, name_shaped=name_shaped))
    return list(terms.values())


def requirement_terms(text: str, keywords: list[str]) -> dict[str, str]:
    """The checkable terms of one job requirement: its keywords plus every
    name-shaped word of its text, without generic posting vocabulary such as
    "experience" or "strong".

    Returns ``{singular form: word as written}``; the key is what gets
    compared, the value (the requirement's own spelling where it has one) is
    for messages shown to the user.
    """
    keyword_words = {
        _singular(token): word for _, word, token in _candidate_words(" ".join(keywords))
    }
    text_words = {_singular(token): word for _, word, token in _candidate_words(text)}
    keys = set(keyword_words) | {term.key for term in find_terms(text, frozenset())}
    return {
        key: text_words.get(key) or keyword_words[key] for key in sorted(keys - GENERIC_JOB_WORDS)
    }


def name_tokens(*values: str | None) -> frozenset[str]:
    """Singular tokens of names (job title, company, candidate name) that a
    cover letter may mention without citing evidence."""
    return frozenset(_singular(token) for value in values if value for token in tokenize(value))


@dataclass(frozen=True)
class GroundingContext:
    """What every statement of one draft is checked against, besides the
    evidence it cites."""

    # Every word of the confirmed profile version.
    profile: Vocabulary
    # Union of requirement_terms() over the job's requirements.
    requirement_keywords: frozenset[str]
    # Job title, company and candidate name: allowed in the cover letter only.
    letter_terms: frozenset[str]
    # The job title and company exactly as written. A figure inside one of
    # them ("SDE 2", "3M") names the job; it is not a claim about the candidate.
    job_names: tuple[str, ...] = ()


# ---- (b) Numbers with unit and context ---------------------------------------------

_SMALL_NUMBER_WORDS = (
    "two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen"
).split()
_TENS_WORDS = "twenty thirty forty fifty sixty seventy eighty ninety".split()
_UNIT_WORDS = "one two three four five six seven eight nine".split()
_NUMBER_WORD_PATTERN = re.compile(
    rf"\b(?:(?P<tens>{'|'.join(_TENS_WORDS)})(?:[- ](?P<unit>{'|'.join(_UNIT_WORDS)}))?"
    rf"|(?P<small>{'|'.join(_SMALL_NUMBER_WORDS)}))\b",
    re.IGNORECASE,
)


def spell_numbers_as_digits(text: str) -> str:
    """Rewrite spelled-out numbers from two to ninety-nine as digits, so "five
    engineers" in the evidence supports "5 engineers" in a statement. A lone
    "one" is left alone because it is usually a pronoun."""

    def replace(match: re.Match[str]) -> str:
        if match["small"]:
            return str(_SMALL_NUMBER_WORDS.index(match["small"].lower()) + 2)
        value = 20 + 10 * _TENS_WORDS.index(match["tens"].lower())
        if match["unit"]:
            value += _UNIT_WORDS.index(match["unit"].lower()) + 1
        return str(value)

    return _NUMBER_WORD_PATTERN.sub(replace, text)


_NUMBER_PATTERN = re.compile(
    r"""
    (?<![\w.])                                   # not inside a word ("p95") or a decimal
    (?P<currency>[$€£])?
    (?P<digits>\d{1,3}(?:,\d{3})+|\d+)
    (?P<decimal>\.\d+)?
    (?:(?P<suffix>k|mm|m|bn|b)\b|\ (?P<magnitude>hundred|thousand|million|billion)\b)?
    (?P<plus>\+)?
    (?:\ ?(?P<percent>%|percent\b|pct\b)|(?P<times>x\b|×|\ times\b|[- ]fold\b))?
    (?!\w)                                       # "2nd", "3D" and "100ms" are words
    """,
    re.IGNORECASE | re.VERBOSE,
)
_MULTIPLIERS = {
    "k": 1e3,
    "m": 1e6,
    "mm": 1e6,
    "b": 1e9,
    "bn": 1e9,
    "hundred": 1e2,
    "thousand": 1e3,
    "million": 1e6,
    "billion": 1e9,
}
_AT_LEAST_BEFORE = re.compile(
    r"(?:over|more than|at least|above|exceeding|upwards of|greater than)\s+$", re.IGNORECASE
)
# A length of service such as "5+ years": handled by the escalation check,
# which asks for the literal wording, instead of the numeric check.
_TENURE_PATTERN = re.compile(r"\d+(?:\.\d+)?\+?\s*(?:years?|yrs?)\b", re.IGNORECASE)

# Words that say a figure went up or down. They do not say what was measured,
# so they are ignored when comparing what a figure is about.
_CHANGE_STEMS = frozenset(
    _stem(word)
    for word in (
        "improve improved increase increased reduce reduced decrease decreased cut grow "
        "grew boost boosted raise raised lower lowered save saved achieve achieved deliver "
        "delivered drive drove result resulting gain gained drop dropped approximately "
        "nearly roughly around average"
    ).split()
)

# How many words on each side of a figure in a statement are compared.
CLAIM_CONTEXT_WORDS = 5
# How many content words on each side of a figure in the evidence say what it
# measures. Deliberately fewer than on the statement's side: in "Improved unit
# test coverage by 20% by adding pytest suites for the billing module" the
# figure measures "unit test coverage"; the clause that follows says how the
# result was achieved. With a wider window its words would make an unrelated
# statement ("lowered the AWS bill by 20%") look as if it were about the same thing.
EVIDENCE_CONTEXT_WORDS = 3


@dataclass(frozen=True)
class NumberMention:
    """A figure as written in some text, in comparable form."""

    value: float  # 5000.0 for "5,000", "5k" and "5 thousand"
    unit: str  # "%", "x", a currency symbol, or "" for a bare count
    at_least: bool  # written as "5,000+" or "over 5,000"
    start: int
    end: int
    raw: str


def find_numbers(text: str) -> list[NumberMention]:
    """Every figure in ``text`` (which should already be passed through
    spell_numbers_as_digits)."""
    mentions = []
    for match in _NUMBER_PATTERN.finditer(text):
        value = float(match["digits"].replace(",", "") + (match["decimal"] or ""))
        scale = (match["suffix"] or match["magnitude"] or "").lower()
        if match["percent"]:
            unit = "%"
        elif match["times"]:
            unit = "x"
        else:
            unit = match["currency"] or ""
        mentions.append(
            NumberMention(
                value=value * _MULTIPLIERS.get(scale, 1.0),
                unit=unit,
                at_least=bool(match["plus"])
                or bool(_AT_LEAST_BEFORE.search(text[: match.start()])),
                start=match.start(),
                end=match.end(),
                raw=match.group().strip(),
            )
        )
    return mentions


def _same_quantity(claimed: NumberMention, found: NumberMention) -> bool:
    """Same value and unit. "5,000+" also needs the evidence to say "5,000+"
    or "over 5,000"; plain "5,000" is fine when the evidence says "5,000+"."""
    return (
        math.isclose(claimed.value, found.value, rel_tol=1e-9)
        and claimed.unit == found.unit
        and (found.at_least or not claimed.at_least)
    )


def _content_stems(tokens: Iterable[str]) -> set[str]:
    """Stems of the words that describe a subject: no figures, no change words."""
    stems = {_stem(token) for token in tokens if not _NUMBER_LIKE.fullmatch(token)}
    return stems - _CHANGE_STEMS


def _claim_context(text: str, mention: NumberMention) -> set[str]:
    """Stems of the few content words on each side of a figure in a statement."""
    before = tokenize(text[: mention.start])[-CLAIM_CONTEXT_WORDS:]
    after = tokenize(text[mention.end :])[:CLAIM_CONTEXT_WORDS]
    return _content_stems(before + after)


def _nearest_content_stems(tokens: Iterable[str]) -> set[str]:
    """Stems of the first EVIDENCE_CONTEXT_WORDS tokens that describe a
    subject (figures and change words such as "reduced" are passed over)."""
    stems: set[str] = set()
    for token in tokens:
        stems |= _content_stems([token])
        if len(stems) == EVIDENCE_CONTEXT_WORDS:
            break
    return stems


def _evidence_context(text: str, mention: NumberMention) -> set[str]:
    """Stems of the content words next to a figure in the evidence: the nearest
    few on each side, without leaving the sentence (or line) that holds it."""
    start, end = 0, len(text)
    for sentence in split_sentences(text):
        if sentence.start <= mention.start < sentence.end:
            start, end = sentence.start, sentence.end
    before = tokenize(text[start : mention.start])
    after = tokenize(text[mention.end : end])
    return _nearest_content_stems(reversed(before)) | _nearest_content_stems(after)


def number_findings(
    claim: str, cited_texts: list[str], *, only_with_unit: bool = False
) -> list[Finding]:
    """Every figure in a statement must occur in the cited evidence with the
    same unit and about the same thing.

    "About the same thing" means: the words around the figure in the statement
    share at least one content word with the words right next to the figure in
    the evidence. So "Improved API latency by 20%" is rejected when the cited
    evidence says "Reduced cloud costs by 20%": the figure is there, but it
    measures something else. A figure with no describing words around it
    ("a 20% improvement") cannot be contradicted this way and is accepted when
    the figure itself is in the evidence.

    ``only_with_unit`` restricts the check to metrics such as "20%", "3x" or
    "$2M" and skips bare counts; coverage uses it for requirement texts, where
    a bare number is often a version ("Python 3") rather than a result.
    """
    claim = spell_numbers_as_digits(claim)
    evidence = [spell_numbers_as_digits(text) for text in cited_texts]
    occurrences = [(text, mention) for text in evidence for mention in find_numbers(text)]

    findings: list[Finding] = []
    for mention in find_numbers(claim):
        if _TENURE_PATTERN.match(claim, mention.start) or (only_with_unit and not mention.unit):
            continue
        same = [(text, found) for text, found in occurrences if _same_quantity(mention, found)]
        if not same:
            message = f'"{mention.raw}" does not appear in the cited evidence.'
        else:
            context = _claim_context(claim, mention)
            if not context or any(context & _evidence_context(text, found) for text, found in same):
                continue
            message = f'"{mention.raw}" appears in the cited evidence, but about something else.'
        finding = Finding("unsupported", message)
        if finding not in findings:
            findings.append(finding)
    return findings


# ---- (c) Technology names, proper nouns and requirement keywords -------------------


def term_findings(
    claim: str, cited: Vocabulary, grounding: GroundingContext, allowed: frozenset[str]
) -> list[Finding]:
    """Names and requirement keywords in a statement must occur in the cited
    evidence. A term that is elsewhere in the confirmed profile needs review
    (it may be true, but this evidence does not show it); a term that is
    nowhere in the profile is unsupported, e.g. "Kubernetes" when the profile
    only mentions Docker."""
    findings = []
    for term in find_terms(claim, grounding.requirement_keywords):
        if term.key in allowed or cited.has(term):
            continue
        if grounding.profile.has(term):
            findings.append(
                Finding(
                    "needs_review",
                    f'"{term.raw}" is in your profile but not in the evidence cited here.',
                )
            )
        else:
            findings.append(
                Finding(
                    "unsupported",
                    f'"{term.raw}" does not appear anywhere in your confirmed profile.',
                )
            )
    return findings


# ---- (d) Expertise and seniority escalation ----------------------------------------

_ESCALATION_PATTERN = re.compile(
    r"""
    \b(?:
        expert(?:s|ise|ly)?
      | extensive(?:ly)?
      | proficien(?:t|cy)
      | advanced
      | mastery | mastered
      | seasoned | veteran | specialist
      | highly\s+(?:skilled|experienced)
      | deep(?:ly)?\s+(?:knowledge|experience|experienced|expertise|understanding|familiar)
      | in[- ]depth
      | world[- ]class
      | \d+(?:\.\d+)?\+?\s*(?:years?|yrs?)
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _squash(text: str) -> str:
    """Lower case, digits for number words and single spaces, for literal comparison."""
    return " ".join(spell_numbers_as_digits(text).lower().split())


def find_escalations(text: str) -> list[str]:
    """Phrases that claim a level of expertise or a length of service."""
    phrases: list[str] = []
    for match in _ESCALATION_PATTERN.finditer(spell_numbers_as_digits(text)):
        phrase = " ".join(match.group().split())
        if phrase.lower() not in (seen.lower() for seen in phrases):
            phrases.append(phrase)
    return phrases


def escalation_findings(claim: str, cited_texts: list[str]) -> list[Finding]:
    """Wording such as "expert", "extensive experience" or "5+ years" must be
    used literally by the cited evidence. Otherwise the statement may turn
    familiarity into expertise, so it needs review."""
    evidence = _squash("\n".join(cited_texts))
    return [
        Finding(
            "needs_review",
            f'"{phrase}" is stronger or more specific than the wording of the cited evidence.',
        )
        for phrase in find_escalations(claim)
        if phrase.lower() not in evidence
    ]


# ---- (f) Connective text -----------------------------------------------------------


def without_job_names(text: str, grounding: GroundingContext) -> str:
    """``text`` with every exact mention of the job title and company blanked
    out, for the figure checks of a cover letter. "I am applying for the SDE 2
    role at 3M" then contains no figure, while "I led 2 teams" still does."""
    for name in grounding.job_names:
        text = re.sub(re.escape(name), " ", text, flags=re.IGNORECASE)
    return text


def connective_findings(
    text: str, grounding: GroundingContext, allowed: frozenset[str]
) -> list[Finding]:
    """Checks for cover-letter text that cites no evidence and was marked as
    connective. The mark is not trusted:

    - a figure makes the sentence a factual claim without evidence
      (unsupported), unless it is part of the job's own title or company name;
    - a name, or a requirement keyword that is nowhere in the confirmed
      profile, needs review: "my kubernetes skills match this role" claims a
      skill, however the model labelled it;
    - expertise wording needs review.

    A requirement keyword that the profile does contain is left alone. A
    sentence about the job ("excited to work on your data pipelines")
    naturally repeats the posting's words without claiming anything new.
    """
    findings = []
    if find_numbers(spell_numbers_as_digits(without_job_names(text, grounding))):
        findings.append(
            Finding("unsupported", "This sentence contains a figure but cites no evidence.")
        )
    for term in find_terms(text, grounding.requirement_keywords):
        keyword_the_profile_has = not term.name_shaped and grounding.profile.has(term)
        if term.key in allowed or keyword_the_profile_has:
            continue
        findings.append(
            Finding(
                "needs_review",
                f'This sentence cites no evidence but mentions "{term.raw}". '
                "Check that it does not claim experience you do not have.",
            )
        )
    for phrase in find_escalations(text):
        findings.append(
            Finding("needs_review", f'This sentence cites no evidence but says "{phrase}".')
        )
    return findings


# ---- (e) Skills --------------------------------------------------------------------


def evidence_mentioning(
    name: str, evidence_tokens: dict[str, frozenset[str]], preferred: Iterable[str], limit: int = 2
) -> list[str]:
    """IDs of evidence records that contain every word of a skill name.

    ``evidence_tokens`` maps evidence_id to that record's singular tokens.
    A skill may only be listed when at least one record of the confirmed
    profile mentions it; the returned records become its citations. Records
    in ``preferred`` (the ones the model cited) are tried first.
    """
    wanted = {_singular(token) for token in tokenize(name)}
    if not wanted:
        return []
    preferred = [evidence_id for evidence_id in preferred if evidence_id in evidence_tokens]
    ordered = [*preferred, *(key for key in evidence_tokens if key not in preferred)]
    return [key for key in ordered if wanted <= evidence_tokens[key]][:limit]


def singular_tokens(text: str) -> frozenset[str]:
    """Singular tokens of one evidence text, the form evidence_mentioning expects."""
    return frozenset(_singular(token) for token in tokenize(text))


# ---- One statement -----------------------------------------------------------------

_SEVERITY_RANK = {"needs_review": 1, "unsupported": 2}


def _verdict(findings: list[Finding], clean_status: ValidationStatus) -> ClaimVerdict:
    """The strictest finding decides the status; every message is kept."""
    if not findings:
        return ClaimVerdict(clean_status, [])
    worst = max(findings, key=lambda finding: _SEVERITY_RANK[finding.severity])
    return ClaimVerdict(worst.severity, [finding.message for finding in findings])


def validate_claim(
    text: str,
    cited_texts: list[str],
    grounding: GroundingContext,
    *,
    section: Section,
    factual: bool = True,
) -> ClaimVerdict:
    """Validate one statement against the text of the evidence it validly cites.

    ``cited_texts`` holds only evidence that passed the membership check (and,
    for a resume bullet, belongs to the bullet's own role or project).
    ``factual`` is the model's own label and matters only for a cover-letter
    paragraph without evidence: labelled connective, it goes through
    connective_findings; everywhere else a statement without evidence is
    unsupported.
    """
    allowed = (
        grounding.letter_terms | LETTER_FORMALITIES if section == "cover_letter" else frozenset()
    )
    if not cited_texts:
        if factual or section != "cover_letter":
            return ClaimVerdict("unsupported", [NO_EVIDENCE_MESSAGE])
        return _verdict(connective_findings(text, grounding, allowed), "not_applicable")

    # Only the cover letter may name the job, so only there is its name set aside.
    figures_text = without_job_names(text, grounding) if section == "cover_letter" else text
    findings = [
        *number_findings(figures_text, cited_texts),
        *term_findings(text, Vocabulary.of(cited_texts), grounding, allowed),
        *escalation_findings(text, cited_texts),
    ]
    return _verdict(findings, "supported")


# ---- Whole documents ---------------------------------------------------------------


@dataclass(frozen=True)
class LocatedClaim:
    """A statement together with where it sits in the draft."""

    section: Section
    claim: Claim
    # The role or project a resume bullet is listed under.
    record_id: str | None = None


def iter_claims(
    resume: ResumeDocument | None, cover_letter: CoverLetter | None
) -> Iterator[LocatedClaim]:
    """Every statement of a draft, in reading order."""
    if resume is not None:
        for claim in resume.summary:
            yield LocatedClaim("summary", claim)
        entry_sections: list[tuple[Section, list]] = [
            ("experience", resume.experience),
            ("projects", resume.projects),
            ("education", resume.education),
            ("certifications", resume.certifications),
        ]
        for section, entries in entry_sections:
            for entry in entries:
                for claim in entry.bullets:
                    yield LocatedClaim(section, claim, entry.record_id)
        for claim in resume.skills:
            yield LocatedClaim("skills", claim)
    if cover_letter is not None:
        for claim in cover_letter.paragraphs:
            yield LocatedClaim("cover_letter", claim)


def summarize_validation(
    resume: ResumeDocument | None, cover_letter: CoverLetter | None, validated_at: datetime | None
) -> ValidationSummary:
    """Count statements by status. The draft needs revalidation while any
    statement still has the status "user_edited"."""
    statuses = [located.claim.validation_status for located in iter_claims(resume, cover_letter)]
    pending = statuses.count("user_edited")
    return ValidationSummary(
        state="needs_revalidation" if pending else "validated",
        needs_review_count=statuses.count("needs_review"),
        unsupported_count=statuses.count("unsupported"),
        user_edited_count=pending,
        validated_at=validated_at,
    )


# ---- Optional semantic verifier ----------------------------------------------------

_VERIFIER_DOWNGRADES: dict[str, Severity] = {
    "partially_supported": "needs_review",
    "unsupported": "unsupported",
}
_VERIFIER_DEFAULT_REASON = "the cited evidence may not support this statement."


async def semantic_findings(
    provider: AIProvider, checks: list[LLMClaimCheck]
) -> tuple[dict[str, Finding], Usage]:
    """Ask the provider whether each statement follows from its evidence.

    Returns a finding per check ID only where the verifier disagrees:
    "partially supported" becomes needs_review and "unsupported" becomes
    unsupported. A "supported" answer returns nothing, so the verifier can make
    a status stricter but never better. Its answers are a second opinion from
    a language model, not a guarantee; IDs it invents are ignored.
    """
    if not checks:
        return {}, Usage()
    verification, usage = await provider.verify_claims(checks)
    known_ids = {check.id for check in checks}
    findings = {}
    for result in verification.results:
        severity = _VERIFIER_DOWNGRADES.get(result.verdict)
        if severity and result.id in known_ids:
            reason = " ".join(result.reason.split())[:300] or _VERIFIER_DEFAULT_REASON
            findings[result.id] = Finding(severity, f"Semantic check: {reason}")
    return findings, usage


def stricter(verdict: ClaimVerdict, finding: Finding) -> ClaimVerdict:
    """Combine a deterministic verdict with a verifier finding: the status only
    ever moves towards unsupported."""
    current_rank = _SEVERITY_RANK.get(verdict.status, 0)
    status = finding.severity if _SEVERITY_RANK[finding.severity] > current_rank else verdict.status
    return ClaimVerdict(status, [*verdict.warnings, finding.message])

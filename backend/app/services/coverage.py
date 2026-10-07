"""Requirement coverage: how well the retrieved evidence backs each job requirement.

The model proposes a status per requirement and explains it in one sentence.
The server checks the proposal against the evidence. It can lower a rating; it
never raises one above what the model proposed.

What is compared (see requirement_groups):

- whole keywords as the posting writes them ("GitHub Actions", "CI/CD",
  "relational database"), never the single words they are made of;
- alternatives count once: of a list joined by "or" or "/", given as examples
  ("such as", "e.g.") or in brackets, or introduced by "at least one", one
  mention is enough;
- descriptor words ("front-end", "tools", "experience") and the hiring
  company's own name are not checked at all.

The rules for a "supported" or "partial" proposal, in the order applied:

1. no valid evidence cited: "uncertain";
2. the requirement names a metric ("reduced costs by 20%") that the cited
   evidence does not show for the same thing: "uncertain";
3. a qualification whose only keyword is a named technology that the whole
   confirmed profile never mentions: "missing";
4. the cited evidence answers fewer than half of the keyword groups:
   "uncertain"; at least half but not all: "supported" becomes "partial";
5. a duty or general statement with nothing checkable that was rated
   "partial": "uncertain", because half credit there is a guess.

"missing" is always worded as a statement about the profile text, never about
the person.

The model's sentence is shown unless it names something (a technology, a job
keyword, a figure, expertise wording) found in neither the requirement, the
cited evidence nor the job's own title and company. That keeps text the model
made up, or was told to write by an instruction planted in a posting, out of
the coverage panel. When the server lowers a rating it keeps that sentence and
adds its own reason after it.

The summary is evidence coverage, nothing more. It is not a suitability score,
an ATS score or a hiring probability.
"""

import re
from dataclasses import dataclass, replace

from app.schemas.generations import CoverageItem, CoverageStatus, CoverageSummary
from app.schemas.jobs import Requirement
from app.services.textutil import tokenize
from app.services.validation import (
    GENERIC_JOB_WORDS,
    Term,
    Vocabulary,
    escalation_findings,
    find_terms,
    name_tokens,
    number_findings,
)

MISSING_ADVICE = "That does not mean you lack it: add it to your profile if you have it."
MISSING_RATIONALE = (
    f"No evidence for this requirement was found in the supplied profile. {MISSING_ADVICE}"
)
NOT_ASSESSED_RATIONALE = "The draft did not assess this requirement."
NO_EVIDENCE_RATIONALE = (
    "The draft rated this requirement without citing evidence from your profile, "
    "so the rating could not be confirmed."
)
INCONCLUSIVE_RATIONALE = "The evidence is inconclusive."
RELATED_RATIONALE = "The cited evidence relates to this requirement."
NOTHING_TO_CHECK_REASON = (
    "This requirement describes a duty or a general quality rather than something "
    "a profile can show, so it is not counted as covered."
)
MAX_RATIONALE_CHARS = 600

# Duties and general statements describe the job rather than a qualification.
# The names in them may be the employer's own products, so only the keywords
# the job analysis listed are checked there, and the server never turns one
# into "missing".
DUTY_CATEGORIES = frozenset({"responsibility", "other"})

# Words that describe instead of naming: the parts of compound descriptors
# ("front-end", "full-stack", "large-scale", "hands-on") and category nouns
# ("CI/CD tools", "REST APIs", "deep learning framework", "Master's degree").
# On their own they say nothing a profile could be checked for.
DESCRIPTOR_WORDS = GENERIC_JOB_WORDS | name_tokens(
    "front end back full stack hands large scale real time high performance cross "
    "functional well tested tool tooling set api framework library platform provider "
    "technology fundamental principle concept practice bachelor master degree"
)


# The same thing under another common name: the phrase on the left is also
# answered when every word of one name on the right occurs in the evidence.
# Kept to pairs that postings and resumes really mix, because a wrong pair
# would let a rating stand that the evidence does not bear out. "Google Cloud
# Run" answers "GCP"; the reverse direction ("Google Cloud Platform" asked,
# "GCP" written) is the acronym rule.
_OTHER_NAMES = {
    "gcp": ("Google Cloud",),
    "aws": ("Amazon Web Services",),
    "k8s": ("Kubernetes",),
    "kubernetes": ("k8s",),
    "javascript": ("JS",),
    "typescript": ("TS",),
    "postgres": ("PostgreSQL",),
    "postgresql": ("Postgres",),
}
# The words of each name in the form evidence words are compared in.
ALIASES: dict[str, tuple[frozenset[str], ...]] = {
    label: tuple(name_tokens(name) for name in names) for label, names in _OTHER_NAMES.items()
}


@dataclass(frozen=True)
class CoverageProposal:
    """The model's rating of one requirement, after its evidence aliases were
    mapped back and every reference outside the retrieved context was dropped."""

    status: CoverageStatus
    evidence_ids: list[str]
    rationale: str


# ---- What a requirement asks for: phrases and groups of alternatives ---------------


@dataclass(frozen=True)
class Phrase:
    """One keyword or name of a requirement, compared as a whole."""

    label: str  # as the posting writes it, for messages
    terms: tuple[Term, ...]  # its words that the evidence must contain
    acronym: str  # "ml" for "machine learning"; empty for a single word
    is_keyword: bool  # listed by the job analysis, not just a capitalised word
    start: int = -1  # position in the requirement text; -1 when it is not there
    end: int = -1

    def mentioned_in(self, vocabulary: Vocabulary) -> bool:
        """All of the phrase's words occur in the text, or its acronym does
        ("ML" answers "machine learning"), or another common name of it does
        (see ALIASES)."""
        return (
            all(vocabulary.has(term) for term in self.terms)
            or (bool(self.acronym) and self.acronym in vocabulary.exact)
            or any(words <= vocabulary.exact for words in ALIASES.get(self.label.lower(), ()))
        )


def _is_spelled_as_name(word: str) -> bool:
    """A capital letter, a digit or a technology symbol ("AWS", "s3", "c++").
    Names are compared exactly, ordinary words ("testing") by their stem."""
    return any(char.isupper() or char.isdigit() or char in "+#." for char in word)


def _phrase(
    label: str, ignored: frozenset[str], *, is_keyword: bool, start: int = -1, end: int = -1
) -> Phrase | None:
    """``label`` as a phrase, or None when no word of it is worth checking."""
    words = find_terms(label, name_tokens(label))  # every word of the label, once
    terms = tuple(
        replace(word, name_shaped=_is_spelled_as_name(word.raw))
        for word in words
        if word.key not in DESCRIPTOR_WORDS and word.key not in ignored
    )
    if not terms:
        return None
    initials = "".join(token[0] for token in tokenize(label))
    acronym = initials if len(initials) > 1 else ""
    return Phrase(label, terms, acronym, is_keyword, start, end)


def _whole(text: str, plural: str = "") -> str:
    """Regex source matching ``text`` as a whole word or phrase, optionally
    followed by ``plural`` (a regex for a plural ending)."""
    return r"(?<![\w+#])" + re.escape(text) + plural + r"(?![\w+#])"


def _keyword_spans(text: str, keywords: list[str]) -> tuple[list[tuple[int, int]], list[str]]:
    """Where each keyword stands in the requirement text (plural allowed), and
    the keywords that are not in it. Longer keywords are placed first, so
    "github" inside "github actions" is not counted a second time."""
    spans: list[tuple[int, int]] = []
    elsewhere: list[str] = []
    for keyword in sorted(dict.fromkeys(keywords), key=len, reverse=True):
        match = re.search(_whole(keyword, plural="(?:e?s)?"), text, re.IGNORECASE)
        if match is None:
            elsewhere.append(keyword)
        elif not any(start < match.end() and match.start() < end for start, end in spans):
            spans.append(match.span())
    return spans, elsewhere


def _name_spans(text: str, taken: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Capitalised or technology-spelled words of the text that no keyword
    covers. Neighbours separated only by a space, "/" or "-" form one span,
    so "CI/CD" is one name."""
    located = []
    for term in find_terms(text, frozenset()):
        match = re.search(_whole(term.raw), text)
        covered = match is None or any(
            start < match.end() and match.start() < end for start, end in taken
        )
        if not covered and term.key not in DESCRIPTOR_WORDS:
            located.append(match.span())
    spans: list[tuple[int, int]] = []
    for start, end in sorted(located):
        if spans and text[spans[-1][1] : start] in (" ", "/", "-"):
            spans[-1] = (spans[-1][0], end)
        else:
            spans.append((start, end))
    return spans


# What may stand between two items of one list: "A, B and C", "A or B", "A/B".
_LIST_SEPARATOR = re.compile(r"(?:\s|,|/|\b(?:and|or|an?|the)\b)*", re.IGNORECASE)
_OR = re.compile(r"/|\bor\b", re.IGNORECASE)
_AND = re.compile(r"\band\b", re.IGNORECASE)
# Wording right before a list that makes its items alternatives or examples:
# "tools such as X and Y", "at least one of X, Y", "a cloud provider (X, Y, Z)".
_ANY_OF_CUE = re.compile(
    r"(?:\b(?:such as|e\.g|for example|for instance|like|including|at least one|"
    r"one or more|one of|any of|either)\b(?:\W+\w+){0,4}\W*|\([^)]*)$",
    re.IGNORECASE,
)
# Between a phrase and a cue, these mean the cue belongs to something else:
# in "Python and cloud tools such as AWS", AWS is not an example of Python.
_CLAUSE_BREAK = re.compile(r"[,;:.)]|\b(?:and|or|plus|with|for|in|on|using|via)\b", re.IGNORECASE)
# What follows an open list: "Computer Science or a related field".
_OPEN_ENDED = re.compile(
    r"\W*or\s+(?:an?\s+|any\s+)?(?:other|related|similar|equivalent|comparable)\b",
    re.IGNORECASE,
)


def _plain_list_groups(
    items: list[Phrase], joins: list[str], open_ended: bool
) -> list[list[Phrase]]:
    """Groups of a list that no cue introduces. "and" separates, "or" makes
    alternatives of the items around it and of the commas before it:

        "Helm, Kafka and Docker"       -> [Helm] [Kafka] [Docker]
        "AWS, GCP or Azure"            -> [AWS, GCP, Azure]
        "Python and FastAPI or Flask"  -> [Python] [FastAPI, Flask]

    ``open_ended`` says the list goes on with "or similar": that is one more,
    unnamed alternative of its last items.
    """
    groups: list[list[Phrase]] = []
    run, run_has_or = [items[0]], False
    for item, join in zip(items[1:], joins, strict=True):
        if join == "and":
            groups += [run] if run_has_or else [[phrase] for phrase in run]
            run, run_has_or = [], False
        run_has_or = run_has_or or join == "or"
        run.append(item)
    groups += [run] if run_has_or or open_ended else [[phrase] for phrase in run]
    return groups


def _join_kind(gap: str) -> str:
    """How a list separator joins its two items: "or", "and" or "comma"."""
    if _OR.search(gap):  # includes "and/or"
        return "or"
    return "and" if _AND.search(gap) else "comma"


def _groups_in_text(text: str, phrases: list[Phrase]) -> list[list[Phrase]]:
    """Split the phrases found in the requirement text (in text order) into
    groups of alternatives; one mention per group is enough."""
    groups: list[list[Phrase]] = []
    position = 0
    while position < len(phrases):
        # One list: phrases with nothing but separators between them.
        items, joins = [phrases[position]], []
        while position + len(items) < len(phrases):
            following = phrases[position + len(items)]
            gap = text[items[-1].end : following.start]
            if not _LIST_SEPARATOR.fullmatch(gap):
                break
            joins.append(_join_kind(gap))
            items.append(following)
        lead_in = text[phrases[position - 1].end if position else 0 : items[0].start]
        position += len(items)

        cue = _ANY_OF_CUE.search(lead_in)
        if cue is None:
            open_ended = bool(_OPEN_ENDED.match(text, items[-1].end))
            groups += _plain_list_groups(items, joins, open_ended)
        elif groups and not _CLAUSE_BREAK.search(lead_in[: cue.start()]):
            # "a relational database such as PostgreSQL", "retrieval-augmented
            # generation (RAG)": the category or full name right before the
            # cue is one more alternative.
            groups[-1] += items
        else:
            groups.append(items)
    # "Computer Science or a related field": an open list demands nothing.
    return [group for group in groups if not _OPEN_ENDED.match(text, group[-1].end)]


def requirement_groups(requirement: Requirement, ignored: frozenset[str]) -> list[list[Phrase]]:
    """What the evidence must mention for this requirement: a list of groups,
    each a list of alternative phrases. ``ignored`` holds the words of the
    hiring company's name, which no candidate's profile is expected to contain.

    Phrases are the keywords of the job analysis and, for a qualification, any
    other capitalised or technology-spelled name in the text. Keywords that
    are not in the requirement text (they come from the quoted passage) form
    one group of alternatives, because how the posting joined them is unknown.
    """
    text = requirement.text
    keyword_spans, elsewhere = _keyword_spans(text, requirement.keywords)
    spans = [(span, True) for span in keyword_spans]
    if requirement.category not in DUTY_CATEGORIES:
        spans += [(span, False) for span in _name_spans(text, keyword_spans)]
    in_text = [
        _phrase(text[start:end], ignored, is_keyword=is_keyword, start=start, end=end)
        for (start, end), is_keyword in sorted(spans)
    ]
    groups = _groups_in_text(text, [phrase for phrase in in_text if phrase])
    not_in_text = [_phrase(keyword, ignored, is_keyword=True) for keyword in elsewhere]
    if any(not_in_text):
        groups.append([phrase for phrase in not_in_text if phrase])
    return groups


# ---- The job and profile every requirement is checked against ----------------------


@dataclass(frozen=True)
class CoverageContext:
    """What the checks of one job share."""

    # Every word of the confirmed profile, cited or not.
    profile: Vocabulary
    # The phrases of all the job's requirements. One of them in a rationale is
    # a claim about the applicant, however the model spells it.
    job_phrases: tuple[Phrase, ...]
    # Job title and company as written; a rationale may repeat them.
    job_names: tuple[str, ...]
    company_words: frozenset[str]


def coverage_context(
    requirements: list[Requirement],
    evidence_texts: dict[str, str],
    *,
    job_title: str | None = None,
    company: str | None = None,
) -> CoverageContext:
    company_words = name_tokens(company)
    return CoverageContext(
        profile=Vocabulary.of(evidence_texts.values()),
        job_phrases=tuple(
            phrase
            for requirement in requirements
            for group in requirement_groups(requirement, company_words)
            for phrase in group
        ),
        job_names=tuple(name for name in (job_title, company) if name),
        company_words=company_words,
    )


# ---- The rationale shown to the user -----------------------------------------------


def _listed(labels: list[str], last: str) -> str:
    """["A"] -> "A"; ["A", "B", "C"] with last="or" -> "A, B or C"."""
    if len(labels) < 2:
        return "".join(labels)
    return f"{', '.join(labels[:-1])} {last} {labels[-1]}"


def _as_sentence(text: str) -> str:
    return text if text.endswith((".", "!", "?")) else f"{text}."


def _is_grounded(
    rationale: str, requirement: Requirement, cited_texts: list[str], context: CoverageContext
) -> bool:
    """Whether a sentence written by the model may be shown to the user.

    A rationale explains a rating by pointing at the requirement and at the
    cited evidence, so its names, job keywords, figures and expertise wording
    must come from there (or from the job's own title and company). A keyword
    of another requirement counts even in lower case at the start of a
    sentence, where its spelling does not give it away as a name.
    """
    allowed_texts = [requirement.text, *cited_texts, *context.job_names]
    allowed = Vocabulary.of(allowed_texts)
    written = Vocabulary.of([rationale])
    return (
        all(allowed.has(term) for term in find_terms(rationale, frozenset()))
        and all(
            phrase.mentioned_in(allowed)
            for phrase in context.job_phrases
            if phrase.mentioned_in(written)
        )
        and not number_findings(rationale, allowed_texts)
        and not escalation_findings(rationale, allowed_texts)
    )


def _model_sentence(
    proposal: CoverageProposal,
    requirement: Requirement,
    cited_texts: list[str],
    context: CoverageContext,
) -> str | None:
    """The model's rationale, tidied, when it may be shown; otherwise None."""
    text = " ".join(proposal.rationale.split())[:MAX_RATIONALE_CHARS]
    if text and _is_grounded(text, requirement, cited_texts, context):
        return text
    return None


def _evidence_clause(found: list[str], absent: list[str]) -> str:
    """Server wording that names, in the posting's order, the phrases the cited
    evidence mentions and, if any, those it does not. No full stop, so a
    reason can follow."""
    mentions = f"The cited evidence mentions {_listed(found, 'and')}"
    return f"{mentions} but not {_listed(absent, 'or')}" if absent else mentions


def _lowered_rationale(sentence: str | None, absent: list[Phrase], reason: str) -> str:
    """The model's sentence followed by the server's reason for a lower rating.
    The sentence is left out when it names a phrase the evidence lacks: it may
    be the very claim ("has run Kubernetes") that the check just rejected."""
    if sentence is None:
        return reason
    written = Vocabulary.of([sentence])
    if any(phrase.mentioned_in(written) for phrase in absent):
        return reason
    return f"{_as_sentence(sentence)} {reason}"


# ---- Checking one proposal ---------------------------------------------------------


def _check_against_evidence(
    requirement: Requirement,
    proposal: CoverageProposal,
    cited_texts: list[str],
    context: CoverageContext,
) -> tuple[CoverageStatus, str]:
    """Lower a "supported"/"partial" rating that the evidence does not bear
    out (rules 2 to 5 of the module docstring). Returns the status and the
    rationale to use; the status is never higher than the proposed one."""
    # A metric named by the requirement must be in the evidence, about the same
    # thing: "test coverage up 20%" does not show "cloud costs down 20%". The
    # model's sentence is not kept here: it explained a figure nobody showed.
    figures = number_findings(requirement.text, cited_texts, only_with_unit=True)
    if figures:
        problems = " ".join(finding.message for finding in figures)
        return (
            "uncertain",
            f"This requirement names a figure the evidence does not show. {problems}",
        )

    sentence = _model_sentence(proposal, requirement, cited_texts, context)
    groups = requirement_groups(requirement, context.company_words)
    is_duty = requirement.category in DUTY_CATEGORIES
    if not groups:
        if is_duty and proposal.status == "partial":
            return "uncertain", _lowered_rationale(sentence, [], NOTHING_TO_CHECK_REASON)
        return proposal.status, sentence or RELATED_RATIONALE

    phrases = [phrase for group in groups for phrase in group]
    lone_keyword = len(phrases) == 1 and phrases[0].is_keyword
    if lone_keyword and not is_duty and not phrases[0].mentioned_in(context.profile):
        label = phrases[0].label
        return (
            "missing",
            f"No evidence of {label} was found in the supplied profile. {MISSING_ADVICE}",
        )

    cited = Vocabulary.of(cited_texts)
    found = [phrase.label for phrase in phrases if phrase.mentioned_in(cited)]
    unanswered = [
        group for group in groups if not any(phrase.mentioned_in(cited) for phrase in group)
    ]
    absent = [phrase for group in unanswered for phrase in group]
    absent_labels = [phrase.label for phrase in absent]
    if 2 * len(unanswered) > len(groups):
        reason = (
            f"The cited evidence does not mention {_listed(absent_labels, 'or')}, "
            "so this rating could not be confirmed."
        )
        return "uncertain", _lowered_rationale(sentence, absent, reason)
    # From here at least half of the groups are answered, so ``found`` has names.
    clause = _evidence_clause(found, absent_labels)
    if unanswered and proposal.status == "supported":
        reason = f"{clause}, so this is rated partial."
        return "partial", _lowered_rationale(sentence, absent, reason)
    return proposal.status, sentence or f"{clause}."


def assess_requirement(
    requirement: Requirement,
    proposal: CoverageProposal | None,
    evidence_texts: dict[str, str],
    *,
    context: CoverageContext | None = None,
) -> CoverageItem:
    """Turn the model's proposal for one requirement into the stored coverage item.

    ``evidence_texts`` maps evidence_id to the text the validators search (the
    same text statements are validated against) for the whole confirmed
    profile. ``context`` is shared by the requirements of one job; without it
    the requirement is assessed on its own.
    """
    context = context or coverage_context([requirement], evidence_texts)
    cited_ids = proposal.evidence_ids if proposal else []
    cited_texts = [evidence_texts[key] for key in cited_ids if key in evidence_texts]
    if proposal is None:
        status, rationale = "uncertain", NOT_ASSESSED_RATIONALE
    elif proposal.status == "missing":
        status, rationale = "missing", MISSING_RATIONALE
    elif proposal.status == "uncertain":
        status = "uncertain"
        rationale = (
            _model_sentence(proposal, requirement, cited_texts, context) or INCONCLUSIVE_RATIONALE
        )
    elif not proposal.evidence_ids:
        status, rationale = "uncertain", NO_EVIDENCE_RATIONALE
    else:
        status, rationale = _check_against_evidence(requirement, proposal, cited_texts, context)
    return CoverageItem(
        requirement_id=requirement.requirement_id,
        requirement_text=requirement.text,
        importance=requirement.importance,
        status=status,
        # "missing" means nothing in the profile answers the requirement.
        evidence_ids=cited_ids if status != "missing" else [],
        rationale=rationale,
    )


def build_coverage(
    requirements: list[Requirement],
    proposals: dict[str, CoverageProposal],
    evidence_texts: dict[str, str],
    *,
    job_title: str | None = None,
    company: str | None = None,
) -> list[CoverageItem]:
    """One coverage item per requirement, in the job's own order. ``proposals``
    is keyed by requirement_id; ratings for unknown requirements never get here.
    ``evidence_texts`` covers the whole confirmed profile."""
    context = coverage_context(requirements, evidence_texts, job_title=job_title, company=company)
    return [
        assess_requirement(
            requirement,
            proposals.get(requirement.requirement_id),
            evidence_texts,
            context=context,
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

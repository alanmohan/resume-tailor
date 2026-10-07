"""Profile ingestion: labelled source text -> a draft profile for the user to review.

The provider proposes records; this module checks them before anything is
stored, because model output is not trusted:

- The original text of every source is stored untouched. The model reads a
  whitespace-normalised copy and returns verbatim quotes, never offsets. Each
  quote is located in the original text and becomes a SourceRef. A quote that
  cannot be located flags the item for review; it is never accepted silently.
- Titles, organisations, dates and skills must occur in the source text.
- A statement quoted from a line that addresses an AI system ("ignore all
  previous instructions ...") is left out, with a note on the record.
- The same role found in two sources is merged into one record only when title
  and organisation match. Different dates are shown as a Conflict, never
  resolved here. Distinct roles are never merged.
- A name, e-mail address or phone number that differs between sources is a
  Conflict as well; the first source's value is shown until the user decides.
- A disagreement is listed once, whether the model reported it, the server
  found it, or both.
- Source lines that ended up in no part of the draft, and a missing name,
  contact detail or education entry, are reported as profile-level notices.
  Notices inform the user and never block confirmation.
"""

import logging
import re
from dataclasses import dataclass

from app.config import Settings
from app.errors import InputTooLarge, ValidationFailed, VersionConflict
from app.logging_config import log_event
from app.providers.base import (
    AIProvider,
    LLMBullet,
    LLMConflict,
    LLMContactItem,
    LLMExtraction,
    LLMRecord,
    LLMSource,
)
from app.ratelimit import QuotaService
from app.repositories import Repositories
from app.schemas.common import new_id, utc_now
from app.schemas.documents import ProfileDoc, SourceDoc
from app.schemas.profiles import (
    MAX_BULLETS_PER_RECORD,
    MAX_LINKS,
    MAX_RECORDS,
    MAX_SKILLS_PER_RECORD,
    Conflict,
    ConflictValue,
    Contact,
    IngestRequest,
    Profile,
    ProfileBullet,
    ProfileNotice,
    ProfileRecord,
    SourceInput,
    SourceRef,
)
from app.security import SessionContext
from app.services.sessions import SessionService
from app.services.textutil import (
    LIMITED_EXPOSURE,
    NormalizedText,
    content_hash,
    fold_text,
    locate_quote,
    looks_like_instruction,
    normalize_whitespace,
    surrounding_lines,
    tokenize,
)

logger = logging.getLogger(__name__)

# Text limits of PATCH /api/profile (app/schemas/profiles.py). Extracted text is
# kept within them so a draft can always be saved again after the user edits it.
MAX_TITLE_CHARS = 300
MAX_LINE_CHARS = 300
MAX_DATE_CHARS = 80
MAX_SUMMARY_CHARS = 6000
# The text fields of a record that the model fills in, with their limits.
RECORD_TEXT_LIMITS = {
    "title": MAX_TITLE_CHARS,
    "organization": MAX_LINE_CHARS,
    "location": MAX_LINE_CHARS,
    "start_date": MAX_DATE_CHARS,
    "end_date": MAX_DATE_CHARS,
    "summary": MAX_SUMMARY_CHARS,
}
MAX_BULLET_CHARS = 4000
MAX_SKILL_CHARS = 120
MAX_LINK_CHARS = 400
MAX_CONFLICTS = 50
MAX_CONFLICT_TEXT_CHARS = 500

SPAN_NOT_FOUND = "source span not found"
AMBIGUOUS = "The extraction marked this item as ambiguous."
NO_DATES = "No dates were found for this item."
TOO_MANY_BULLETS = f"Only the first {MAX_BULLETS_PER_RECORD} statements were kept."
INSTRUCTION_LEFT_OUT = (
    "A line that reads like an instruction to an AI system, not like a fact about you, "
    "was left out."
)

# Codes of the profile-level notices (part of the API, see ProfileNotice).
NOTICE_UNCAPTURED = "source_text_not_captured"
NOTICE_NO_NAME = "missing_name"
NOTICE_NO_CONTACT = "missing_contact_details"
NOTICE_NO_EDUCATION = "missing_education"
# A line with fewer content words is a heading or a label, not worth a notice.
MIN_NOTICE_WORDS = 3
# A line counts as captured when at least this share of its characters is quoted.
MIN_QUOTED_SHARE = 0.5
MAX_NOTICE_EXCERPTS = 3
MAX_NOTICE_EXCERPT_CHARS = 120

# Categories for which missing dates are worth a second look by the user.
DATED_CATEGORIES = frozenset({"employment", "education"})
DATE_FIELDS = {"start_date": "start dates", "end_date": "end dates"}
# Contact details on which two sources can disagree, with the wording used for
# the conflict. A location is written too freely to compare ("Columbus, OH").
CONTACT_CONFLICT_FIELDS = {
    "name": "names",
    "email": "e-mail addresses",
    "phone": "phone numbers",
}
# Conflict fields the server checks itself. The model's own field label is free
# text ("dates", "other"), so these findings are compared with it by value.
SERVER_CHECKED_FIELDS = frozenset(
    [*DATE_FIELDS, *(f"contact_{name}" for name in CONTACT_CONFLICT_FIELDS)]
)
_OPEN_ENDED = frozenset({"present", "current", "now", "ongoing"})
# A list marker at the start of a quoted statement: "- ", "* ", "\u2022 ".
_LIST_MARKER = re.compile(r"^[-*\u2022\u2023\u25e6\u00b7\u2013\u2014]\s+")
# A whole line of a skills section given as ONE skill: a category label, a
# colon and the list ("Backend & Cloud: Python, Flask, AWS S3.").
_LABELLED_SKILL_LINE = re.compile(r"^(?P<label>(?:[^:,;()]|\([^()]*\)){2,60}):\s+(?P<items>\S.*)$")
# The commas and semicolons between the items of such a list; one inside
# brackets belongs to its item ("AWS (S3, EC2)").
_SKILL_SEPARATOR = re.compile(r"[,;]\s*(?![^()]*\))")
_LEADING_CONJUNCTION = re.compile(r"^(?:and|or)\s+", re.IGNORECASE)


# ---- Small text helpers ------------------------------------------------------------


def _clean(value: str | None) -> str | None:
    """Trimmed text, or None when nothing is left."""
    return (value or "").strip() or None


def _date_key(value: str) -> str:
    """Comparison form of a date string, so "July 2022" equals "Jul. 2022" and
    "Current" equals "Present". Only used to detect disagreement between
    sources; the stored date always stays exactly as written."""
    words = re.findall(r"[a-z]+|\d+", value.casefold())
    return " ".join(
        "present" if word in _OPEN_ENDED else word[:3] if word.isalpha() else word for word in words
    )


def unique_texts(items: list[str]) -> list[str]:
    """Drop repeats (ignoring case and spacing), keeping the first of each."""
    seen: set[str] = set()
    kept = []
    for item in items:
        key = fold_text(item)
        if key not in seen:
            seen.add(key)
            kept.append(item)
    return kept


def _bounded(value: str | None, limit: int, label: str, reasons: list[str]) -> str | None:
    """Clean ``value`` and cut it to ``limit`` characters, noting the cut in
    ``reasons`` so the user sees that the text is incomplete."""
    text = _clean(value)
    if text and len(text) > limit:
        reasons.append(f"{label} was longer than {limit} characters and was shortened.")
        return text[:limit].rstrip()
    return text


# ---- Sources -----------------------------------------------------------------------


@dataclass(frozen=True)
class PreparedSource:
    """A source document, the alias the model knows it by, and the
    whitespace-normalised copy of its text that the model reads."""

    alias: str
    doc: SourceDoc
    normalized: NormalizedText
    # The normalised text in comparison form, for whole-term lookups.
    folded: str

    def locate(self, quote: str, search_from: int = 0) -> SourceRef | None:
        """Find ``quote`` in this source and return a reference whose offsets
        point into the ORIGINAL text, or None when it does not occur."""
        span = locate_quote(
            self.doc.text, self.normalized.text, self.normalized.offsets, quote, search_from
        )
        if span is None:
            return None
        start, end = span
        return SourceRef(
            source_id=self.doc.source_id,
            source_label=self.doc.label,
            start=start,
            end=end,
            excerpt=self.doc.text[start:end],
        )

    def mentions(self, term: str) -> bool:
        """True when ``term`` occurs as a whole term (so "Java" is not found
        inside "JavaScript"). Works for one-character skills such as "C"."""
        pattern = r"(?<![\w+#])" + re.escape(fold_text(term)) + r"(?![\w+#])"
        return re.search(pattern, self.folded) is not None


def prepare_source(alias: str, doc: SourceDoc) -> PreparedSource:
    normalized = normalize_whitespace(doc.text)
    return PreparedSource(
        alias=alias, doc=doc, normalized=normalized, folded=fold_text(normalized.text)
    )


# ---- Reading model output ----------------------------------------------------------


@dataclass
class _Entry:
    """A profile record being assembled, plus what merging needs to know."""

    record: ProfileRecord
    # Aliases of the sources this record was read from.
    aliases: set[str]
    # Where the record's summary paragraph was found, if it has one.
    summary_ref: SourceRef | None


def _read_bullet(
    llm_bullet: LLMBullet, source: PreparedSource | None, search_from: int
) -> ProfileBullet | None:
    """A statement of a record. Its text is the model's verbatim quote on one
    line, without a leading list marker; its reference is where that quote
    occurs in the source."""
    reasons: list[str] = []
    statement = _LIST_MARKER.sub("", " ".join(llm_bullet.quote.split()))
    text = _bounded(statement, MAX_BULLET_CHARS, "The statement", reasons)
    if text is None:
        return None
    ref = source.locate(llm_bullet.quote, search_from) if source else None
    if ref is None:
        reasons.append(SPAN_NOT_FOUND)
    return ProfileBullet(
        bullet_id=new_id(),
        text=text,
        source_ref=ref,
        provenance="extracted",
        needs_review=bool(reasons),
        review_reasons=reasons,
    )


def skill_items(skill: str) -> list[str]:
    """The skills named by one entry of the model's skill list: normally the
    entry itself.

    Models sometimes return a whole line of a skills section as one skill
    ("Backend & Cloud: Python, Flask, AWS S3."). Such a line is no skill name:
    printed on a resume it reads as a sentence, and "Flask" could not be found
    as a skill of its own. It is split into the items it lists; the category
    label is a heading and is dropped. Two cases stay whole: a label that
    qualifies the skills ("Coursework only: TensorFlow, Keras"), because the
    qualifier must not be lost, and a line with a single item, which cannot be
    told apart from a skill whose name has a colon in it."""
    line = _LABELLED_SKILL_LINE.match(skill)
    if line is None or LIMITED_EXPOSURE.search(line["label"]):
        return [skill]
    items = [
        _LEADING_CONJUNCTION.sub("", piece.strip()).strip(" .")
        for piece in _SKILL_SEPARATOR.split(line["items"])
    ]
    items = [item for item in items if item]
    return items if len(items) > 1 else [skill]


def _read_skills(skills: list[str], sources: list[PreparedSource], reasons: list[str]) -> list[str]:
    """Keep only skills that are written in a source. A skill that is nowhere
    in the supplied text was invented by the model, so it is left out and the
    record is flagged. A labelled line given as one skill is read as the
    skills it lists (see skill_items)."""
    kept = []
    for raw in skills:
        entry = _clean(raw)
        for skill in skill_items(entry) if entry else []:
            if len(skill) > MAX_SKILL_CHARS:
                reasons.append(f"A skill longer than {MAX_SKILL_CHARS} characters was left out.")
            elif not any(source.mentions(skill) for source in sources):
                reasons.append(f'The skill "{skill}" was left out: it is not in the source text.')
            else:
                kept.append(skill)
    return kept


def _is_instruction(text: str, ref: SourceRef | None, source: PreparedSource | None) -> bool:
    """Whether a statement is, or was quoted from a line that is, addressed to
    an AI system. Such a line must not become a fact (and later evidence) just
    because it is written in the source."""
    if ref is None or source is None:
        return looks_like_instruction(text)
    return looks_like_instruction(surrounding_lines(source.doc.text, ref.start, ref.end))


def _read_record(
    llm_record: LLMRecord, by_alias: dict[str, PreparedSource], sources: list[PreparedSource]
) -> _Entry | None:
    """Turn one extracted record into a profile record with source references
    and review flags. Returns None for a record without a title."""
    reasons: list[str] = []
    labels = {name: f"The {name.replace('_', ' ')}" for name in RECORD_TEXT_LIMITS}
    facts = {
        name: _bounded(getattr(llm_record, name), limit, labels[name], reasons)
        for name, limit in RECORD_TEXT_LIMITS.items()
    }
    if facts["title"] is None:
        return None

    source = by_alias.get(llm_record.source)
    header_ref = source.locate(llm_record.header_quote) if source else None
    if header_ref is None:
        reasons.append(SPAN_NOT_FOUND)
    if llm_record.ambiguous:
        notes = [note for raw in llm_record.ambiguity_notes if (note := _clean(raw))]
        reasons.extend(note[:MAX_CONFLICT_TEXT_CHARS] for note in notes or [AMBIGUOUS])

    # A record's text follows its header, so each search starts where the
    # previous match ended. This picks the right occurrence of a repeated sentence.
    search_from = {llm_record.source: header_ref.start if header_ref else 0}
    summary_ref = None
    if facts["summary"]:
        if source:
            summary_ref = source.locate(facts["summary"], search_from[llm_record.source])
        if _is_instruction(facts["summary"], summary_ref, source):
            facts["summary"], summary_ref = None, None
            reasons.append(INSTRUCTION_LEFT_OUT)
    if source:
        # Every stated fact must be written in the source it is attributed to.
        # The title of a skill group is only a label ("Skills"), not a fact.
        unchecked = {"title"} if llm_record.category == "skill" else set()
        for name, value in facts.items():
            if value and name not in unchecked and source.locate(value) is None:
                reasons.append(f"{labels[name]} was not found in the source text.")

    bullets = []
    for llm_bullet in llm_record.bullets:
        bullet_source = by_alias.get(llm_bullet.source)
        bullet = _read_bullet(llm_bullet, bullet_source, search_from.get(llm_bullet.source, 0))
        if bullet is None:
            continue
        if _is_instruction(bullet.text, bullet.source_ref, bullet_source):
            reasons.append(INSTRUCTION_LEFT_OUT)
            continue
        bullets.append(bullet)
        if bullet.source_ref is not None:
            search_from[llm_bullet.source] = bullet.source_ref.end

    record = ProfileRecord(
        record_id=new_id(),
        category=llm_record.category,
        **facts,
        bullets=bullets,
        skills=_read_skills(llm_record.skills, sources, reasons),
        source_ref=header_ref,
        provenance="extracted",
        review_reasons=reasons,
    )
    return _Entry(record=record, aliases={llm_record.source}, summary_ref=summary_ref)


@dataclass(frozen=True)
class _ContactFact:
    """A contact detail that is written in the source it was read from."""

    field: str
    value: str
    alias: str
    ref: SourceRef


def _verified_contact(
    items: list[LLMContactItem], by_alias: dict[str, PreparedSource]
) -> list[_ContactFact]:
    """The contact details that occur in their source, in the order sources
    were submitted. A value that is not written there is left out, so the user
    fills it in instead of confirming something the model made up."""
    facts = []
    for item in items:
        value = _clean(item.value)
        source = by_alias.get(item.source)
        limit = MAX_LINK_CHARS if item.field == "link" else MAX_LINE_CHARS
        if value is None or len(value) > limit or source is None:
            continue
        ref = source.locate(value)
        if ref is not None:
            facts.append(_ContactFact(item.field, value, item.source, ref))
    return facts


def _read_contact(facts: list[_ContactFact]) -> Contact:
    """The first value of each contact field, and every distinct link. A later
    source that states a different value is reported by _contact_conflicts."""
    fields: dict[str, str] = {}
    links: list[str] = []
    for fact in facts:
        if fact.field == "link":
            links.append(fact.value)
        else:
            fields.setdefault(fact.field, fact.value)
    return Contact(**fields, links=unique_texts(links)[:MAX_LINKS])


def _same_contact_value(field: str, first: str, second: str) -> bool:
    """Whether two sources mean the same contact detail although they write it
    differently: a phone number with or without country code and punctuation,
    a name with or without a middle name or initial, an e-mail address in
    another letter case."""
    if field == "phone":
        a, b = (re.sub(r"\D", "", value) for value in (first, second))
        return a.endswith(b) or b.endswith(a)
    if field == "name":
        a, b = (set(re.findall(r"\w+", value.casefold())) for value in (first, second))
        return a <= b or b <= a
    return fold_text(first) == fold_text(second)


def _contact_conflicts(facts: list[_ContactFact]) -> list[Conflict]:
    """Names, e-mail addresses and phone numbers that differ between sources.

    The profile shows the first source's value (see _read_contact). A different
    value in a later source is not dropped silently: it becomes a conflict, so
    the user decides which one belongs on the resume before confirming. Two
    values inside ONE source (a work and a private address) are no disagreement.
    """
    conflicts = []
    for name, label in CONTACT_CONFLICT_FIELDS.items():
        stated = [fact for fact in facts if fact.field == name]
        if not stated:
            continue
        accepted = [fact for fact in stated if fact.alias == stated[0].alias]
        differing: list[_ContactFact] = []
        for fact in stated:
            known = [*accepted, *differing]
            if not any(_same_contact_value(name, fact.value, other.value) for other in known):
                differing.append(fact)
        if differing:
            conflicts.append(
                Conflict(
                    conflict_id=new_id(),
                    field=f"contact_{name}",
                    description=f"The sources give different {label}.",
                    values=[
                        ConflictValue(value=fact.value, source_ref=fact.ref)
                        for fact in (stated[0], *differing)
                    ],
                )
            )
    return conflicts


# ---- Merging duplicates and detecting conflicts ------------------------------------


def _merge_key(record: ProfileRecord) -> tuple[str, str, str]:
    return record.category, fold_text(record.title), fold_text(record.organization or "")


def _conflicting_date_fields(first: ProfileRecord, second: ProfileRecord) -> list[str]:
    """The date fields both records state, but differently."""
    return [
        name
        for name in DATE_FIELDS
        if (a := getattr(first, name))
        and (b := getattr(second, name))
        and _date_key(a) != _date_key(b)
    ]


def _find_merge_target(entries: list[_Entry], incoming: _Entry) -> tuple[_Entry | None, list[str]]:
    """Decide whether ``incoming`` repeats a record already seen.

    Returns ``(target, conflicting_date_fields)``; ``target`` is None when the
    record is new. Records are the same only if category, title and
    organisation match, and then:

    1. Dates agree (or one side has none): the same record, merge quietly.
    2. Dates differ and the two come from different sources: still one role,
       described inconsistently. Merge, and report the differing dates as a
       conflict for the user to decide.
    3. Dates differ within one source (or several candidates exist): separate
       periods in the same job. Keep them apart.
    """
    same_role = [e for e in entries if _merge_key(e.record) == _merge_key(incoming.record)]
    for entry in same_role:
        if not _conflicting_date_fields(entry.record, incoming.record):
            return entry, []
    if len(same_role) == 1 and not same_role[0].aliases & incoming.aliases:
        return same_role[0], _conflicting_date_fields(same_role[0].record, incoming.record)
    return None, []


def _date_conflict(name: str, kept: ProfileRecord, other: ProfileRecord) -> Conflict:
    role = kept.title + (f" at {kept.organization}" if kept.organization else "")
    return Conflict(
        conflict_id=new_id(),
        field=name,
        description=f"The sources give different {DATE_FIELDS[name]} for {role}.",
        record_ids=[kept.record_id],
        values=[
            ConflictValue(value=getattr(kept, name), source_ref=kept.source_ref),
            ConflictValue(value=getattr(other, name), source_ref=other.source_ref),
        ],
    )


def _merge_into(target: _Entry, incoming: _Entry) -> None:
    """Add what ``incoming`` knows to ``target``. The first record's own fields
    win; nothing it states is overwritten. Repeated bullets and skills are
    removed afterwards by _finish_record."""
    kept, other = target.record, incoming.record
    for name in ("location", *DATE_FIELDS):
        if getattr(kept, name) is None:
            setattr(kept, name, getattr(other, name))
    if other.summary and fold_text(other.summary) != fold_text(kept.summary or ""):
        # A second description of the same role is kept as a statement with its
        # own source reference instead of replacing the first one.
        kept.bullets.append(
            ProfileBullet(
                bullet_id=new_id(),
                text=other.summary,
                source_ref=incoming.summary_ref,
                provenance="extracted",
                needs_review=incoming.summary_ref is None,
                review_reasons=[] if incoming.summary_ref else [SPAN_NOT_FOUND],
            )
        )
    kept.bullets.extend(other.bullets)
    kept.skills.extend(other.skills)
    kept.review_reasons.extend(other.review_reasons)
    target.aliases |= incoming.aliases


def _finish_record(record: ProfileRecord) -> ProfileRecord:
    """Remove repeats inside the record, apply the size limits and set the
    review flag from the collected reasons."""
    reasons = list(record.review_reasons)
    seen: set[str] = set()
    bullets = []
    for bullet in record.bullets:
        key = fold_text(bullet.text).rstrip(".")
        if key not in seen:
            seen.add(key)
            bullets.append(bullet)
    if len(bullets) > MAX_BULLETS_PER_RECORD:
        reasons.append(TOO_MANY_BULLETS)
    if record.category in DATED_CATEGORIES and not (record.start_date or record.end_date):
        reasons.append(NO_DATES)

    record.bullets = bullets[:MAX_BULLETS_PER_RECORD]
    record.skills = unique_texts(record.skills)[:MAX_SKILLS_PER_RECORD]
    record.review_reasons = unique_texts(reasons)
    record.needs_review = bool(reasons)
    return record


def _drop_repeated_skills(records: list[ProfileRecord]) -> list[ProfileRecord]:
    """List each skill once across the skill groups: a later group loses the
    skills an earlier group already names, and a group left empty is dropped.
    ("Python" in "Languages" and again in a second source's "Skills" list.)"""
    seen: set[str] = set()
    kept = []
    for record in records:
        if record.category == "skill":
            record.skills = [skill for skill in record.skills if fold_text(skill) not in seen]
            seen.update(fold_text(skill) for skill in record.skills)
            if not record.skills and not record.bullets and not record.summary:
                continue
        kept.append(record)
    return kept


def _located_passages(record: ProfileRecord) -> list[SourceRef]:
    """Where a record's header and statements were found in the sources."""
    refs = [record.source_ref, *(bullet.source_ref for bullet in record.bullets)]
    return [ref for ref in refs if ref is not None]


def _record_quoted(ref: SourceRef, passages: list[tuple[SourceRef, str]]) -> str | None:
    """The record a quoted span was taken from: the one with a header or
    statement that overlaps the span in the same source. ``passages`` pairs
    each located passage with the ID of its profile record."""
    for passage, record_id in passages:
        same_source = passage.source_id == ref.source_id
        if same_source and passage.start < ref.end and ref.start < passage.end:
            return record_id
    return None


def _read_conflict(
    llm_conflict: LLMConflict,
    record_ids: list[str | None],
    by_alias: dict[str, PreparedSource],
    passages: list[tuple[SourceRef, str]],
) -> Conflict | None:
    """A conflict reported by the model. ``record_ids[i]`` is the profile record
    that the model's i-th record ended up in (None if it was dropped).

    The records in conflict are taken from the quotes: each quote is located
    in its source and traced to the record written there. The positions the
    model counted (``record_indexes``) are used only when a quote could not be
    traced, because a model miscounts positions in a long list and would then
    link the conflict to an unrelated record.
    """
    description = _clean(llm_conflict.description)
    if description is None:
        return None
    values = []
    traced: list[str | None] = []
    for llm_value in llm_conflict.values:
        value = _clean(llm_value.value)
        if not value:
            continue
        source = by_alias.get(llm_value.source)
        ref = source.locate(llm_value.quote) if source else None
        values.append(ConflictValue(value=value[:MAX_LINE_CHARS], source_ref=ref))
        traced.append(_record_quoted(ref, passages) if ref else None)
    ids = [record_id for record_id in traced if record_id]
    if not traced or None in traced:
        ids += [
            record_ids[index]
            for index in llm_conflict.record_indexes
            if 0 <= index < len(record_ids) and record_ids[index]
        ]
    return Conflict(
        conflict_id=new_id(),
        field=fold_text(llm_conflict.field).replace(" ", "_") or "other",
        description=description[:MAX_CONFLICT_TEXT_CHARS],
        record_ids=list(dict.fromkeys(ids)),
        values=values,
    )


def _conflict_key(field: str, value: str) -> str:
    """Comparison form of a conflicting value: a date by _date_key ("July 2022"
    equals "Jul 2022"), anything else ignoring case and spacing."""
    return _date_key(value) if field in DATE_FIELDS else fold_text(value)


def _restates(field: str, value: str, shown: str) -> bool:
    """Whether ``shown`` already says ``value``, possibly as part of a longer
    text ("Jun 2022" inside "Jun 2022 - Jul 2024")."""
    key = _conflict_key(field, value)
    return bool(key) and key in _conflict_key(field, shown)


def _same_disagreement(existing: Conflict, candidate: Conflict) -> bool:
    """Whether ``candidate`` is a disagreement that ``existing`` already lists.

    1. The same field of the same records: the plain case.
    2. ``candidate`` was found by the server (dates, contact details) and
       ``existing`` was reported by the model. The model's field label is free
       text ("dates", "other") and the positions it counts can be wrong, so
       the two are compared by value: it is the same disagreement when they
       are about a common record (or the model's entry names none) and the
       model's entry already shows every value of the server's.
    """
    existing_ids, candidate_ids = set(existing.record_ids), set(candidate.record_ids)
    if existing.field == candidate.field and existing_ids == candidate_ids:
        return True
    if candidate.field not in SERVER_CHECKED_FIELDS:
        return False
    related = not existing_ids or bool(existing_ids & candidate_ids)
    return related and all(
        any(_restates(candidate.field, value.value, shown.value) for shown in existing.values)
        for value in candidate.values
    )


def _with_exact_values(shown: list[ConflictValue], found: Conflict) -> list[ConflictValue]:
    """The values of a conflict after the server found the same disagreement:
    every shown value that one of the server's restates is replaced by the
    server's (the stored field value with the line it was read from), the
    others stay, and server values not shown yet are added."""
    values: list[ConflictValue] = []
    for value in shown:
        exact = next(
            (v for v in found.values if _restates(found.field, v.value, value.value)), value
        )
        if exact not in values:
            values.append(exact)
    return values + [value for value in found.values if value not in values]


def _add_conflict(conflicts: list[Conflict], candidate: Conflict) -> None:
    """Add ``candidate`` unless an entry already lists the same disagreement.

    A finding of the server is exact, so its field name, record and values
    then replace the model's label, counted positions and loosely written
    values; the model's sentence stays as the description. Two entries of the
    model about the same field and records are joined value by value.
    """
    for existing in conflicts:
        if not _same_disagreement(existing, candidate):
            continue
        if candidate.field in SERVER_CHECKED_FIELDS:
            existing.field = candidate.field
            existing.record_ids = list(candidate.record_ids)
            existing.values = _with_exact_values(existing.values, candidate)
        else:
            shown = {fold_text(value.value) for value in existing.values}
            existing.values.extend(v for v in candidate.values if fold_text(v.value) not in shown)
        return
    conflicts.append(candidate)


# ---- Profile-level notices ---------------------------------------------------------


def _quoted_characters(
    sources: list[PreparedSource], quoted: list[SourceRef]
) -> dict[str, bytearray]:
    """For every source, one flag per character of its original text: 1 where
    the character lies inside a passage the draft quotes."""
    flags = {source.doc.source_id: bytearray(len(source.doc.text)) for source in sources}
    for ref in quoted:
        flags[ref.source_id][ref.start : ref.end] = b"\x01" * (ref.end - ref.start)
    return flags


def _known_words(contact: Contact, records: list[ProfileRecord]) -> set[str]:
    """The words of the facts the draft holds outside its statements: record
    headers, skills and contact details."""
    texts = [contact.name, contact.email, contact.phone, contact.location, *contact.links]
    for record in records:
        texts += [record.title, record.organization, record.location]
        texts += [record.start_date, record.end_date, *record.skills]
    return {word for text in texts if text for word in tokenize(text)}


def _repeats_known_facts(line: str, known_words: set[str]) -> bool:
    """Whether a line only lists facts the draft already holds, such as a
    "Key technologies: Python, Redis" line whose items became skills, or a
    line with nothing but a role's dates. A label before a colon is ignored."""
    _, colon, listing = line.partition(":")
    return set(tokenize(listing if colon and listing.strip() else line)) <= known_words


def _uncaptured_lines(source: PreparedSource, flags: bytearray, known_words: set[str]) -> list[str]:
    """The lines of a source that say something and are in no part of the draft.

    A line is captured when at least half of it was quoted (as a header,
    statement, summary, skill list or contact detail) or when it only repeats
    facts the draft holds. Headings and labels (fewer than MIN_NOTICE_WORDS
    content words) are not worth a notice, and a line addressed to an AI
    system was left out on purpose.
    """
    missed = []
    start = 0
    for raw in source.doc.text.splitlines(keepends=True):
        end = start + len(raw)
        quoted_share = sum(flags[start:end]) / max(len(raw.strip()), 1)
        start = end
        line = _LIST_MARKER.sub("", raw.strip())
        if len(tokenize(line)) < MIN_NOTICE_WORDS or looks_like_instruction(line):
            continue
        if quoted_share < MIN_QUOTED_SHARE and not _repeats_known_facts(line, known_words):
            missed.append(line)
    return missed


def _uncaptured_message(label: str, lines: list[str]) -> str:
    """ "2 lines of Resume were not captured: "..."; "...". Add ..." with the
    first few lines, shortened, as plain-text excerpts."""
    count = len(lines)
    counted = "1 line" if count == 1 else f"{count} lines"
    verb = "was" if count == 1 else "were"
    excerpts = "; ".join(f'"{_shortened(line)}"' for line in lines[:MAX_NOTICE_EXCERPTS])
    more = f" (and {count - MAX_NOTICE_EXCERPTS} more)" if count > MAX_NOTICE_EXCERPTS else ""
    return (
        f"{counted} of {label} {verb} not captured: {excerpts}{more}. "
        "Add anything that matters to a record."
    )


def _shortened(line: str) -> str:
    """The line itself, or its beginning followed by "..." when it is long."""
    if len(line) <= MAX_NOTICE_EXCERPT_CHARS:
        return line
    return line[:MAX_NOTICE_EXCERPT_CHARS].rstrip() + "..."


def _uncaptured_notices(
    sources: list[PreparedSource],
    quoted: list[SourceRef],
    contact: Contact,
    records: list[ProfileRecord],
) -> list[ProfileNotice]:
    """One notice per source with text that the draft does not hold.

    The model can skip part of a source without saying so (seen on a real
    profile: every result line under two publications). Nothing else would
    tell the user, so the server compares the sources with what was quoted
    from them. The notice informs; it does not block confirmation, because
    leaving text out can be intended (a general summary paragraph).
    """
    flags = _quoted_characters(sources, quoted)
    known_words = _known_words(contact, records)
    notices = []
    for source in sources:
        missed = _uncaptured_lines(source, flags[source.doc.source_id], known_words)
        if missed:
            message = _uncaptured_message(source.doc.label, missed)
            notices.append(ProfileNotice(code=NOTICE_UNCAPTURED, message=message))
    return notices


def completeness_notices(contact: Contact, records: list[ProfileRecord]) -> list[ProfileNotice]:
    """What a resume normally shows but this profile does not hold: a name, a
    way to reach the person, an education entry. Nothing is invented to fill
    the gap and confirmation is not blocked; the user is told, because the
    generated documents would otherwise come out with an empty letterhead or
    without an education section and no word of warning."""
    missing = []
    if not contact.name:
        missing.append(
            (NOTICE_NO_NAME, "No name was found. Add it so that your documents carry your name.")
        )
    if not (contact.email or contact.phone):
        missing.append(
            (
                NOTICE_NO_CONTACT,
                "No e-mail address or phone number was found. Add one so that an "
                "employer can reach you.",
            )
        )
    if not any(record.category == "education" for record in records):
        missing.append(
            (
                NOTICE_NO_EDUCATION,
                "No education was found. Add your degree or programme if you have one; "
                "without it a degree requirement cannot be shown as met.",
            )
        )
    return [ProfileNotice(code=code, message=message) for code, message in missing]


def notices_after_edit(
    stored: list[ProfileNotice], contact: Contact, records: list[ProfileRecord]
) -> list[ProfileNotice]:
    """The notices of a profile the user just edited. Those about source text
    stay (the sources did not change); the completeness ones are worked out
    again, so adding a name removes the notice about the missing name."""
    about_sources = [notice for notice in stored if notice.code == NOTICE_UNCAPTURED]
    return [*about_sources, *completeness_notices(contact, records)]


@dataclass(frozen=True)
class Draft:
    """The checked content of a new profile draft."""

    contact: Contact
    records: list[ProfileRecord]
    conflicts: list[Conflict]
    notices: list[ProfileNotice]


def build_draft(extraction: LLMExtraction, sources: list[PreparedSource]) -> Draft:
    """Check the model's extraction against the sources and assemble the draft."""
    by_alias = {source.alias: source for source in sources}
    entries: list[_Entry] = []
    record_ids: list[str | None] = []
    # Every located header and statement, with the ID of the record it ended up in.
    passages: list[tuple[SourceRef, str]] = []
    summary_refs: list[SourceRef] = []
    date_conflicts: list[Conflict] = []

    for llm_record in extraction.records:
        incoming = _read_record(llm_record, by_alias, sources)
        if incoming is None:
            record_ids.append(None)
            continue
        target, differing = _find_merge_target(entries, incoming)
        if target is None:
            if len(entries) == MAX_RECORDS:
                record_ids.append(None)
                continue
            entries.append(incoming)
            target = incoming
        else:
            date_conflicts.extend(
                _date_conflict(name, target.record, incoming.record) for name in differing
            )
            _merge_into(target, incoming)
        record_ids.append(target.record.record_id)
        passages += [(ref, target.record.record_id) for ref in _located_passages(incoming.record)]
        if incoming.summary_ref is not None:
            summary_refs.append(incoming.summary_ref)

    contact_facts = _verified_contact(extraction.contact, by_alias)
    contact = _read_contact(contact_facts)

    # Model-reported conflicts first, so their descriptions are the ones kept
    # when one of the server's own checks found the same disagreement.
    conflicts: list[Conflict] = []
    reported = [
        _read_conflict(item, record_ids, by_alias, passages) for item in extraction.conflicts
    ]
    found = [*date_conflicts, *_contact_conflicts(contact_facts)]
    for conflict in [*(c for c in reported if c is not None), *found]:
        _add_conflict(conflicts, conflict)

    records = _drop_repeated_skills([_finish_record(entry.record) for entry in entries])
    kept_ids = {record.record_id for record in records}
    for conflict in conflicts:
        conflict.record_ids = [rid for rid in conflict.record_ids if rid in kept_ids]
    quoted = [*(ref for ref, _ in passages), *summary_refs, *(fact.ref for fact in contact_facts)]
    return Draft(
        contact=contact,
        records=records,
        conflicts=conflicts[:MAX_CONFLICTS],
        notices=[
            *_uncaptured_notices(sources, quoted, contact, records),
            *completeness_notices(contact, records),
        ],
    )


# ---- The ingest operation ----------------------------------------------------------


def _check_limits(sources: list[SourceInput], settings: Settings) -> None:
    """Blank sources are rejected by the request schema; the limits that come
    from settings are checked here."""
    if len(sources) > settings.max_sources:
        raise ValidationFailed.for_field(
            "sources",
            f"At most {settings.max_sources} sources can be submitted at once; "
            f"{len(sources)} were sent.",
        )
    total = sum(len(source.text) for source in sources)
    if total > settings.max_profile_chars:
        message = (
            f"The profile text is {total:,} characters long; the limit is "
            f"{settings.max_profile_chars:,}. Shorten or remove a source and try again."
        )
        raise InputTooLarge(message, field_errors=[{"field": "sources", "message": message}])


def _new_sources(
    inputs: list[SourceInput], session: SessionContext, profile_id: str, revision: int
) -> list[PreparedSource]:
    """Source documents for the submitted texts, each with the alias ("S1",
    "S2", ...) under which the model will see it. Nothing is stored yet."""
    now = utc_now()
    return [
        prepare_source(
            f"S{position + 1}",
            SourceDoc(
                source_id=new_id(),
                owner_id=session.owner_id,
                profile_id=profile_id,
                label=source.label,
                source_type=source.source_type,
                text=source.text,
                revision=revision,
                content_hash=content_hash(source.text),
                char_count=len(source.text),
                position=position,
                created_at=now,
                expires_at=session.expires_at,
            ),
        )
        for position, source in enumerate(inputs)
    ]


async def ingest_profile(
    body: IngestRequest,
    *,
    session: SessionContext,
    repos: Repositories,
    provider: AIProvider,
    quotas: QuotaService,
    sessions: SessionService,
    settings: Settings,
) -> Profile:
    """POST /api/profiles/ingest: extract a draft profile from the submitted
    sources. Ingesting again replaces the sources and the draft, keeps the
    profile ID and increases the version.

    Nothing is written until the provider has answered, so a provider failure
    leaves the previous sources and profile exactly as they were.
    """
    _check_limits(body.sources, settings)
    owner_id = session.owner_id
    existing = await repos.profiles.get_for_owner(owner_id)
    previous_sources = await repos.sources.list_for_owner(owner_id)
    await quotas.charge_operation(session, "ingest")
    await quotas.charge_ai_calls(1)

    profile_id = existing.profile_id if existing else new_id()
    revision = max((source.revision for source in previous_sources), default=0) + 1
    prepared = _new_sources(body.sources, session, profile_id, revision)
    # The model reads the normalised copy; the stored text stays untouched.
    extraction, usage = await provider.extract_profile(
        [
            LLMSource(
                alias=source.alias,
                label=source.doc.label,
                source_type=source.doc.source_type,
                text=source.normalized.text,
            )
            for source in prepared
        ]
    )
    draft = build_draft(extraction, prepared)
    now = utc_now()
    profile = ProfileDoc(
        profile_id=profile_id,
        owner_id=owner_id,
        version=existing.version + 1 if existing else 1,
        status="draft",
        contact=draft.contact,
        records=draft.records,
        conflicts=draft.conflicts,
        notices=draft.notices,
        created_at=existing.created_at if existing else now,
        updated_at=now,
        expires_at=session.expires_at,
    )

    source_docs = [source.doc for source in prepared]
    try:
        # The profile goes first: its write is conditional, so if the profile
        # was edited while the provider was working, nothing at all is replaced.
        if existing is None:
            stored = profile if await repos.profiles.create(owner_id, profile) else None
        else:
            stored = await repos.profiles.replace(owner_id, profile, existing.version)
        if stored is None:
            raise VersionConflict(
                "The profile changed while the sources were being processed. "
                "Reload it and try again."
            )
        await repos.sources.replace_for_owner(owner_id, source_docs)
    finally:
        # Also when a write failed: whatever was stored must not survive a
        # "Clear my data" that happened while this request was running.
        await sessions.guard_after_write(session)

    review = stored.review_summary()
    log_event(
        logger,
        logging.INFO,
        "profile_ingested",
        sources=len(source_docs),
        records=len(stored.records),
        needs_review=review.needs_review_count,
        unresolved_conflicts=review.unresolved_conflict_count,
        notices=len(stored.notices),
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
    )
    return stored.to_api(source_docs)

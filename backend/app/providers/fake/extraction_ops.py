"""Deterministic, rule-based profile extraction and job analysis for the fake
provider (tests, end-to-end tests and demo mode).

These are pure functions: no randomness, no network, no clock. FakeProvider
adds call counting, failure injection and usage accounting around them. The
signatures are final.

The rules read plain text in common resume and job-posting layouts:

- a section starts with a heading on its own line (Experience, Education, ...);
- a record starts with a header line such as ``Title - Company (dates)``,
  ``Title | Company | dates`` or ``Title, Company`` followed by a date line;
- statements are bullet lines starting with ``-``, ``*`` or a bullet glyph;
- skills are comma-separated lists with an optional ``Category:`` prefix.

Like the real model, the fake copies text verbatim (it never rewrites or
guesses) and treats instruction-like lines in the data as text to skip, not as
commands.
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field

from app.providers.base import (
    LLMBullet,
    LLMConflict,
    LLMConflictValue,
    LLMContactItem,
    LLMExtraction,
    LLMJobAnalysis,
    LLMJobInput,
    LLMRecord,
    LLMRequirement,
    LLMSource,
)
from app.services.textutil import looks_like_instruction, split_sentences

# ---- Shared line rules -------------------------------------------------------------

_BULLET = re.compile(r"^[-*•‣◦·–—]\s+(?P<text>\S.*)$")

_MONTH = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
    r"|spring|summer|fall|autumn|winter)\.?"
)
_DATE = rf"(?:\b{_MONTH}\s+)?\b(?:19|20)\d{{2}}\b|\b\d{{1,2}}/(?:19|20)\d{{2}}\b"
_DATE_END = rf"(?:{_DATE}|\b(?:present|current|now|ongoing)\b)"
_DATE_RANGE = rf"(?P<start>{_DATE})(?:\s*(?:-|–|—|to|until)\s*(?P<end>{_DATE_END}))?"
# A date or date range at the end of a header line, optionally in brackets or
# after a separator: "... (Aug 2024 - Present)", "... | 2019 - 2021".
_TRAILING_DATES = re.compile(
    rf"\s*(?:[(\[]\s*|[|,–—-]\s*)?{_DATE_RANGE}\s*[)\]]?\s*$", re.IGNORECASE
)
_DATE_LINE = re.compile(rf"^[(\[]?\s*{_DATE_RANGE}\s*[)\]]?$", re.IGNORECASE)

# " - ", " – ", " — " or "|" between the parts of a header line. A hyphen
# inside a word ("Full-Stack") has no spaces around it and does not split.
_HEADER_SEPARATOR = re.compile(r"\s+[–—-]\s+|\s*\|\s*")
MAX_HEADER_WORDS = 14

# Commas, semicolons, pipes and bullets that are not inside parentheses.
_LIST_SEPARATOR = re.compile(r"[,;|•](?![^()]*\))")


def _content_lines(text: str) -> list[str]:
    """Non-empty lines without surrounding spaces. A line that addresses an AI
    system is skipped: the real model is told to treat such text as data and
    not to extract it, and the fake behaves the same way."""
    lines = [line.strip() for line in text.split("\n")]
    return [line for line in lines if line and not looks_like_instruction(line)]


def _heading_key(line: str) -> str:
    """A line in the form used as a key of the section-heading tables."""
    return line.rstrip(":").strip().casefold().replace("\u2019", "'")


# ---- Profile extraction ------------------------------------------------------------


def _heading_table(groups: dict[str, str]) -> dict[str, str]:
    """{"education": "education; education and training"} -> {heading: kind}."""
    return {
        heading.strip(): kind
        for kind, headings in groups.items()
        for heading in headings.split(";")
    }


_PROFILE_SECTIONS = _heading_table(
    {
        "employment": "experience; work experience; professional experience; employment; "
        "employment history; work history",
        "education": "education; education and training",
        "project": "projects; personal projects; selected projects; side projects",
        "certification": "certifications; certificates; licenses and certifications; "
        "licenses & certifications",
        "publication": "publications",
        "achievement": "achievements; awards; honors; honors and awards; accomplishments",
        "skill": "skills; technical skills; core skills; skills and tools; skills & tools",
        # Free-text introductions describe the person in general terms; they are
        # not a record of any category, so the fake leaves them out.
        "ignored": "summary; professional summary; about; about me; profile; objective",
    }
)

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL = re.compile(r"(?:https?://|www\.)[^\s|]+|\b(?:linkedin\.com|github\.com)/[^\s|]+", re.I)
_PHONE = re.compile(r"\+\d[\d\s().-]{7,}\d|\(?\b\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b")
_NAME = re.compile(r"[A-Z][A-Za-z.'-]*(?: [A-Z][A-Za-z.'-]*){1,3}")
_PLACE = r"[A-Z][A-Za-z.'-]*(?: [A-Z][A-Za-z.'-]*)*"
# "City, ST" or "City, State, Country".
_LOCATION = re.compile(rf"{_PLACE}, ?{_PLACE}(?:, ?{_PLACE})?")
_NOT_A_NAME = frozenset({"curriculum vitae", "resume", "cv"})


@dataclass(frozen=True)
class _Header:
    """The parts of a record header line, copied as written."""

    title: str
    organization: str | None
    location: str | None
    start_date: str | None
    end_date: str | None


@dataclass
class _RecordDraft:
    category: str
    header: _Header
    quote: str
    bullets: list[str] = field(default_factory=list)
    summary_lines: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)


def _parse_header(line: str, next_line: str | None) -> _Header | None:
    """Split a header line into title, organisation, location and dates.

    Returns None for a line that does not look like a header: a sentence, a
    long line, or a single phrase without dates.
    """
    if line.endswith((".", ":")) or len(line.split()) > MAX_HEADER_WORDS:
        return None
    dates = _TRAILING_DATES.search(line)
    head = line[: dates.start()] if dates else line
    parts = [part.strip() for part in _HEADER_SEPARATOR.split(head) if part.strip()]
    dated_below = bool(next_line and _DATE_LINE.match(next_line))
    if len(parts) == 1 and "," in parts[0] and (dates or dated_below):
        # "Title, Company" with the dates in brackets or on the next line.
        title, _, organization = parts[0].partition(",")
        parts = [title.strip(), organization.strip()]
    if not parts or (len(parts) == 1 and not dates):
        return None
    return _Header(
        title=parts[0],
        organization=parts[1] if len(parts) > 1 else None,
        location=parts[2] if len(parts) > 2 else None,
        start_date=dates["start"] if dates else None,
        end_date=dates["end"] if dates else None,
    )


def _split_list(text: str) -> list[str]:
    """ "React, Flask, and PostgreSQL" -> ["React", "Flask", "PostgreSQL"]."""
    items = []
    for piece in _LIST_SEPARATOR.split(text):
        item = re.sub(r"^(?:and|or)\s+", "", piece.strip(), flags=re.IGNORECASE).strip(" .")
        if item:
            items.append(item)
    return items


def _skill_group(line: str, section_heading: str) -> _RecordDraft | None:
    """One line of a skills section: "Languages: Python, SQL" or "Python, SQL"."""
    bullet = _BULLET.match(line)
    text = bullet["text"] if bullet else line
    label, colon, listing = text.partition(":")
    if colon and listing.strip() and "," not in label:
        title = label.strip()
    else:
        title, listing = section_heading, text
    skills = _split_list(listing)
    if not skills:
        return None
    header = _Header(title, None, None, None, None)
    return _RecordDraft(category="skill", header=header, quote=text, skills=skills)


def _standalone_record(category: str, text: str) -> _RecordDraft:
    """A list item with no header above it, e.g. one line of an awards list.
    The whole statement becomes the record title unless it parses as a header."""
    header = _parse_header(text, None) or _Header(text, None, None, None, None)
    return _RecordDraft(category=category, header=header, quote=text)


def _parse_records(lines: list[str]) -> tuple[list[str], list[_RecordDraft]]:
    """Walk the lines of one source. Returns the lines before the first
    section heading (name and contact details) and the records found."""
    preamble: list[str] = []
    records: list[_RecordDraft] = []
    section: str | None = None
    section_heading = ""
    current: _RecordDraft | None = None

    for index, line in enumerate(lines):
        heading = _PROFILE_SECTIONS.get(_heading_key(line))
        if heading:
            section, section_heading, current = heading, line.rstrip(":").strip(), None
        elif section is None:
            preamble.append(line)
        elif section == "skill":
            group = _skill_group(line, section_heading)
            if group:
                records.append(group)
        elif section != "ignored":
            next_line = lines[index + 1] if index + 1 < len(lines) else None
            current = _read_record_line(section, line, next_line, current, records)
    return preamble, records


def _read_record_line(
    category: str,
    line: str,
    next_line: str | None,
    current: _RecordDraft | None,
    records: list[_RecordDraft],
) -> _RecordDraft | None:
    """Handle one line inside a record section; returns the record that
    following lines belong to."""
    bullet = _BULLET.match(line)
    if bullet:
        if current is None:
            records.append(_standalone_record(category, bullet["text"]))
        else:
            current.bullets.append(bullet["text"])
        return current

    header = _parse_header(line, next_line)
    if header:
        current = _RecordDraft(category=category, header=header, quote=line)
        records.append(current)
        return current

    if current is None:
        return None
    dates = _DATE_LINE.match(line)
    if dates and current.header.start_date is None and not current.bullets:
        # "Title, Company" followed by a line holding only the dates.
        current.header = _Header(
            current.header.title,
            current.header.organization,
            current.header.location,
            dates["start"],
            dates["end"],
        )
    elif not dates and not current.bullets:
        # Plain text between the header and the first bullet describes the role.
        current.summary_lines.append(line)
    return current


def _to_llm_record(draft: _RecordDraft, alias: str) -> LLMRecord:
    """The record in the shape a model would return it. Every quote is text of
    the source; a role without an employer is reported as ambiguous."""
    header = draft.header
    role_without_employer = draft.category == "employment" and header.organization is None
    return LLMRecord(
        category=draft.category,
        title=header.title,
        organization=header.organization,
        location=header.location,
        start_date=header.start_date,
        end_date=header.end_date,
        summary=" ".join(draft.summary_lines) or None,
        source=alias,
        header_quote=draft.quote,
        bullets=[LLMBullet(source=alias, quote=text) for text in draft.bullets],
        skills=draft.skills,
        ambiguous=role_without_employer,
        ambiguity_notes=(
            ["No organisation could be identified for this role."] if role_without_employer else []
        ),
    )


def _contact_items(preamble: list[str], alias: str) -> list[LLMContactItem]:
    """Name, e-mail, phone, location and links from the lines above the first
    section heading. Every value is copied from the text, so it is its own quote."""
    found: list[tuple[str, str]] = []
    if preamble and _NAME.fullmatch(preamble[0]) and preamble[0].casefold() not in _NOT_A_NAME:
        found.append(("name", preamble[0]))
    for line in preamble:
        found.extend(("email", match.group()) for match in _EMAIL.finditer(line))
        found.extend(("phone", match.group()) for match in _PHONE.finditer(line))
        found.extend(("link", match.group().rstrip(".,)")) for match in _URL.finditer(line))
        for segment in line.split("|"):
            if _LOCATION.fullmatch(segment.strip()):
                found.append(("location", segment.strip()))
    return [
        LLMContactItem(field=name, value=value, source=alias, quote=value) for name, value in found
    ]


def _date_conflicts(records: list[LLMRecord]) -> list[LLMConflict]:
    """Report records that name the same role and organisation in different
    sources but with different dates. Two such entries inside ONE source are
    separate periods in the same job, not a conflict."""
    groups: defaultdict[tuple[str, str, str], list[int]] = defaultdict(list)
    for index, record in enumerate(records):
        if record.category != "skill":
            key = (record.category, record.title.casefold(), (record.organization or "").casefold())
            groups[key].append(index)

    conflicts = []
    for indexes in groups.values():
        sources = [records[index].source for index in indexes]
        if len(set(sources)) < len(sources):
            continue
        for date_field, label in (("start_date", "start dates"), ("end_date", "end dates")):
            dated = [index for index in indexes if getattr(records[index], date_field)]
            if len({getattr(records[index], date_field).casefold() for index in dated}) < 2:
                continue
            first = records[dated[0]]
            role = first.title + (f" at {first.organization}" if first.organization else "")
            conflicts.append(
                LLMConflict(
                    field=date_field,
                    description=f"The sources give different {label} for {role}.",
                    record_indexes=dated,
                    values=[
                        LLMConflictValue(
                            value=getattr(records[index], date_field),
                            source=records[index].source,
                            quote=records[index].header_quote,
                        )
                        for index in dated
                    ],
                )
            )
    return conflicts


def extract_profile(sources: list[LLMSource]) -> LLMExtraction:
    """Extract each source on its own (one record per role per source) and
    report cross-source date conflicts; the server merges exact duplicates."""
    contact: list[LLMContactItem] = []
    records: list[LLMRecord] = []
    for source in sources:
        preamble, drafts = _parse_records(_content_lines(source.text))
        contact.extend(_contact_items(preamble, source.alias))
        records.extend(_to_llm_record(draft, source.alias) for draft in drafts)
    return LLMExtraction(contact=contact, records=records, conflicts=_date_conflicts(records))


# ---- Job analysis ------------------------------------------------------------------

_JOB_SECTIONS = _heading_table(
    {
        "required": "requirements; required; required qualifications; qualifications; "
        "minimum qualifications; minimum requirements; basic qualifications; must have; "
        "must haves; what you need; what you'll need; what we're looking for; who you are; "
        "about you",
        "preferred": "preferred; preferred qualifications; desired qualifications; "
        "nice to have; nice to haves; nice-to-have; bonus; bonus points; pluses",
        "responsibility": "responsibilities; key responsibilities; your responsibilities; "
        "duties; what you'll do; what you will do; you will",
        "about": "about the role; about the job; about the position; about; about us; "
        "about the team; the role; your role; overview; role overview; description; "
        "job description",
    }
)
# Headings that are not in the table are recognised by a telling word, checked
# in this order ("Preferred qualifications" is preferred, not required).
# "unrelated" parts of a posting state no requirements at all.
_HEADING_WORDS = (
    (re.compile(r"\b(?:preferred|nice to have|bonus|desired)\b", re.IGNORECASE), "preferred"),
    (re.compile(r"\b(?:qualifications?|requirements?)\b", re.IGNORECASE), "required"),
    (re.compile(r"\bresponsibilit", re.IGNORECASE), "responsibility"),
    (
        re.compile(
            r"\b(?:benefits?|perks?|compensation|salary|location|how to apply|what we offer"
            r"|equal opportunity)\b",
            re.IGNORECASE,
        ),
        "unrelated",
    ),
)
MAX_HEADING_WORDS = 6

# Small lexicon of technology names recognised as requirement keywords.
_TECH_TERMS = (
    "python java javascript typescript sql golang rust scala kotlin swift ruby php c++ c# "
    "react angular vue node.js fastapi flask django spring pytorch tensorflow scikit-learn "
    "pandas numpy spark hadoop airflow kafka docker kubernetes terraform helm prometheus "
    "grafana aws gcp azure postgresql mysql mongodb redis graphql linux git pytest llm rag "
    "embedding nlp ci/cd"
).split() + [
    "rest api",
    "github actions",
    "machine learning",
    "deep learning",
    "semantic search",
    "retrieval-augmented generation",
    "natural language processing",
]
# Longest first, so "javascript" wins over "java". The lookarounds stop "sql"
# matching inside "postgresql"; an optional plural "s" is allowed.
_TECH_PATTERN = re.compile(
    r"(?<![\w+#.-])(?P<term>"
    + "|".join(re.escape(term) for term in sorted(_TECH_TERMS, key=len, reverse=True))
    + r")s?(?![\w+#-])",
    re.IGNORECASE,
)
_CAPITALISED_TERM = re.compile(r"[A-Z][\w+#.&'/-]*(?:\s+[A-Z][\w+#.&'/-]*)*")
_INNER_CAPITAL_OR_DIGIT = re.compile(r".[A-Z0-9]")
# Capitalised words that name nothing: pronouns and articles in running text,
# and what is left of "REST APIs" once the lexicon term is taken out.
_NOT_A_NAME_WORD = frozenset("a an api apis at i in it our the this we you your".split())

_REQUIREMENT_CUE = re.compile(
    r"\b(?:experience|required|requires?|must|need|proficien\w+|knowledge|familiar\w*"
    r"|degree|skills?|certifi\w+)\b",
    re.IGNORECASE,
)
_PREFERRED_CUE = re.compile(
    r"\b(?:preferred|nice to have|bonus|a plus|ideally|desirable)\b", re.IGNORECASE
)
_EDUCATION_CUE = re.compile(r"\b(?:degree|bachelor|master|ph\.?d|diploma)", re.IGNORECASE)
_CERTIFICATION_CUE = re.compile(r"\b(?:certifi\w+|licen[sc]e\w*)\b", re.IGNORECASE)
_YEARS_CUE = re.compile(r"\b\d+\+?\s*years?\b", re.IGNORECASE)
_SKILL_CUE = re.compile(r"\b(?:skills?|knowledge|proficien\w+|familiar\w*)\b", re.IGNORECASE)

MAX_SUMMARY_SENTENCES = 2


def _capitalised_terms(text: str) -> list[str]:
    """Runs of capitalised words (product names, certifications, fields of
    study), lower-cased. The first word of a sentence is capitalised whatever
    it is, so it only counts when it looks like a product name ("PyTorch")."""
    terms = []
    for sentence in split_sentences(text):
        first_word, _, rest = sentence.text.partition(" ")
        scanned = sentence.text if _INNER_CAPITAL_OR_DIGIT.search(first_word) else rest
        # Lexicon terms are already keywords; cut them out so "Python REST APIs"
        # does not come back as one long term.
        for match in _CAPITALISED_TERM.finditer(_TECH_PATTERN.sub(",", scanned)):
            words = match.group().strip(".,;:'&/-").lower().split()
            while words and words[0] in _NOT_A_NAME_WORD:
                del words[0]
            if len(" ".join(words)) > 1:
                terms.append(" ".join(words))
    return terms


def _keywords(text: str, employer_words: set[str]) -> list[str]:
    """Lower-case keywords of one requirement: technologies from the lexicon,
    then other capitalised terms. Ordinary words are never keywords, and
    neither is the hiring company's own name (``employer_words``)."""
    keywords = [match["term"].lower() for match in _TECH_PATTERN.finditer(text)]
    keywords += [
        term for term in _capitalised_terms(text) if not set(term.split()) <= employer_words
    ]
    return list(dict.fromkeys(keywords))


def _requirement_category(text: str, section: str, has_technology: bool) -> str:
    """Category from the section and the wording, most specific rule first."""
    if section == "responsibility":
        return "responsibility"
    if _EDUCATION_CUE.search(text):
        return "education"
    if _CERTIFICATION_CUE.search(text):
        return "certification"
    if _YEARS_CUE.search(text):
        return "experience"
    if has_technology or _SKILL_CUE.search(text):
        return "skill"
    return "experience" if "experience" in text.casefold() else "other"


def _requirement(text: str, section: str, employer_words: set[str]) -> LLMRequirement:
    """``section`` is "required", "preferred" or "responsibility". A duty is
    reported as an inferred requirement: the posting implies the capability
    without listing it as a qualification."""
    return LLMRequirement(
        text=text,
        category=_requirement_category(text, section, bool(_TECH_PATTERN.search(text))),
        importance="preferred" if section == "preferred" else "required",
        inferred=section == "responsibility",
        quote=text,
        keywords=_keywords(text, employer_words),
    )


def _section_kind(line: str) -> str | None:
    """The kind of section a heading line starts, or None for an ordinary line."""
    kind = _JOB_SECTIONS.get(_heading_key(line))
    if kind:
        return kind
    looks_like_heading = (
        len(line.split()) <= MAX_HEADING_WORDS
        and not line.endswith(".")
        and not _BULLET.match(line)
    )
    if looks_like_heading:
        for pattern, kind in _HEADING_WORDS:
            if pattern.search(line):
                return kind
    return None


def _job_sections(lines: list[str]) -> list[tuple[str, list[str]]]:
    """Group the lines under their headings; lines above the first heading
    belong to an "about" section."""
    sections: list[tuple[str, list[str]]] = [("about", [])]
    for line in lines:
        kind = _section_kind(line)
        if kind:
            sections.append((kind, []))
        else:
            sections[-1][1].append(line)
    return sections


def _section_items(lines: list[str]) -> list[str]:
    """The requirement statements of one section: its bullet lines, or every
    line when the section has no bullets (text pasted without list markers)."""
    bullets = [match["text"] for line in lines if (match := _BULLET.match(line))]
    return bullets or lines


def _unstructured_statements(lines: list[str]) -> list[tuple[str, str]]:
    """Fallback for a posting without recognised headings: ``(text, section)``
    for its bullet lines, or else for sentences that use requirement wording."""
    statements = [match["text"] for line in lines if (match := _BULLET.match(line))]
    if not statements:
        sentences = [span.text for line in lines for span in split_sentences(line)]
        statements = [sentence for sentence in sentences if _REQUIREMENT_CUE.search(sentence)]
    return [
        (text, "preferred" if _PREFERRED_CUE.search(text) else "required") for text in statements
    ]


def _role_summary(about_lines: list[str], job: LLMJobInput) -> str:
    """The first sentences of the posting's introduction, copied as written."""
    prose = " ".join(line for line in about_lines if not _BULLET.match(line))
    sentences = [span.text for span in split_sentences(prose)]
    if sentences:
        return " ".join(sentences[:MAX_SUMMARY_SENTENCES])
    return " at ".join(part for part in (job.title, job.company) if part)


def analyze_job(job: LLMJobInput) -> LLMJobAnalysis:
    """Requirements from the statements under requirement, preferred and
    responsibility headings (or, without such headings, from requirement-like
    sentences), each quoted verbatim, plus a summary from the introduction."""
    lines = _content_lines(job.description)
    sections = _job_sections(lines)
    statements = [
        (text, kind)
        for kind, section_lines in sections
        if kind in ("required", "preferred", "responsibility")
        for text in _section_items(section_lines)
    ]
    employer_words = set((job.company or "").casefold().split())
    about_lines = [
        line for kind, section_lines in sections if kind == "about" for line in section_lines
    ]
    return LLMJobAnalysis(
        role_summary=_role_summary(about_lines, job),
        requirements=[
            _requirement(text, kind, employer_words)
            for text, kind in statements or _unstructured_statements(lines)
        ],
    )

"""Deterministic, rule-based document generation, single-item regeneration and
claim verification for the fake provider (tests, end-to-end tests and demo mode).

These are pure functions: no randomness, no network, no clock. FakeProvider
adds call counting, failure injection and usage accounting around them. The
signatures are final.

The fake never invents content. Every factual sentence it writes is evidence
text, at most shortened to whole sentences, and cites the evidence it was taken
from. That makes its output pass the grounding validators by construction, and
it means the result reads like a list of excerpts rather than polished prose,
which is acceptable for a mode the UI labels "Demo mode".
"""

import re

from app.providers.base import (
    LLMBulletOut,
    LLMClaimCheck,
    LLMContextEvidence,
    LLMContextRequirement,
    LLMCoverageOut,
    LLMEntryOut,
    LLMGeneration,
    LLMGenerationContext,
    LLMRegenResult,
    LLMRegenTarget,
    LLMSkillOut,
    LLMStatement,
    LLMVerdict,
    LLMVerification,
)
from app.services.textutil import split_sentences, tokenize

EXPERIENCE_CATEGORY = "employment"
PROJECT_CATEGORIES = frozenset({"project", "publication", "achievement"})

MAX_BULLETS_PER_RECORD = 4
MAX_BULLET_CHARS = 300
MAX_SKILLS = 12
MAX_SUMMARY_SKILLS = 3
MAX_LETTER_SENTENCES = 4

CLOSING_PARAGRAPH = (
    "Thank you for considering my application. I would welcome the chance to discuss "
    "how my background fits this role."
)

_BULLET_MARK = re.compile(r"^[-*•‣◦·–—]\s+")
# A statement in the first person ("I did not write down the dates") is a
# remark to the reader of the profile. Real resumes do not print such a line as
# a bullet, so demo mode does not either (the same rule as the server's
# fallback bullets in drafting.fill_empty_entries).
_FIRST_PERSON = re.compile(r"\b(?:I|[Mm]y|[Mm]e)\b")


# ---- Text helpers ------------------------------------------------------------------


def _clean(text: str) -> str:
    """One line of text without a leading bullet mark."""
    return _BULLET_MARK.sub("", " ".join(text.split()))


def _leading_sentences(text: str, max_chars: int = MAX_BULLET_CHARS) -> str:
    """The first sentence, plus following whole sentences while the result
    stays within ``max_chars``. Sentences are never cut, so a number is never
    separated from the words that say what it measures."""
    cleaned = _clean(text)
    sentences = [sentence.text for sentence in split_sentences(cleaned)]
    if not sentences:
        return cleaned
    kept = sentences[0]
    for sentence in sentences[1:]:
        if len(kept) + 1 + len(sentence) > max_chars:
            break
        kept = f"{kept} {sentence}"
    return kept


def _as_sentence(text: str) -> str:
    return text if text.endswith((".", "!", "?")) else f"{text}."


def _tokens_by_alias(ctx: LLMGenerationContext) -> dict[str, set[str]]:
    return {evidence.alias: set(tokenize(evidence.text)) for evidence in ctx.evidence}


def _relevance(ctx: LLMGenerationContext) -> dict[str, int]:
    """How many requirements each evidence record was retrieved for."""
    counts = {evidence.alias: 0 for evidence in ctx.evidence}
    for requirement in ctx.requirements:
        for alias in requirement.candidate_evidence:
            if alias in counts:
                counts[alias] += 1
    return counts


def _most_relevant_first(
    evidence: list[LLMContextEvidence], relevance: dict[str, int]
) -> list[LLMContextEvidence]:
    """Stable sort: more requirements first, context order within a tie."""
    return sorted(evidence, key=lambda item: -relevance[item.alias])


# ---- Resume ------------------------------------------------------------------------


def _entries(
    ctx: LLMGenerationContext, relevance: dict[str, int]
) -> tuple[list[LLMEntryOut], list[LLMEntryOut]]:
    """Experience and project entries. Each confirmed role or project with
    retrieved evidence gets its most relevant statements as bullets, in the
    evidence's original order. Evidence that only restates a record's header
    never becomes a bullet; a project retrieved by its header alone is listed
    without bullets, a role without statements is left to the server (which
    lists every role anyway). A statement written in the first person is a
    remark, not an achievement, and never becomes a bullet."""
    category_of = {record.alias: record.category for record in ctx.records}
    evidence_of: dict[str, list[LLMContextEvidence]] = {}
    for item in ctx.evidence:
        if item.record in category_of:
            evidence_of.setdefault(item.record, []).append(item)

    experience: list[LLMEntryOut] = []
    projects: list[LLMEntryOut] = []
    for record_alias, items in evidence_of.items():
        statements = [
            item
            for item in items
            if item.kind == "statement" and not _FIRST_PERSON.search(item.text)
        ]
        chosen = {
            item.alias
            for item in _most_relevant_first(statements, relevance)[:MAX_BULLETS_PER_RECORD]
        }
        entry = LLMEntryOut(
            record=record_alias,
            bullets=[
                LLMBulletOut(text=_leading_sentences(item.text), evidence=[item.alias])
                for item in statements
                if item.alias in chosen
            ],
        )
        if category_of[record_alias] == EXPERIENCE_CATEGORY and entry.bullets:
            experience.append(entry)
        elif category_of[record_alias] in PROJECT_CATEGORIES:
            projects.append(entry)
    return experience, projects


def _skills(ctx: LLMGenerationContext, tokens: dict[str, set[str]]) -> list[LLMSkillOut]:
    """Profile skills that a requirement asks for or that the retrieved
    evidence mentions; requirement matches come first."""
    requirement_keywords = {
        token
        for requirement in ctx.requirements
        for token in tokenize(" ".join(requirement.keywords))
    }
    requested: list[LLMSkillOut] = []
    mentioned: list[LLMSkillOut] = []
    seen: set[str] = set()
    for name in ctx.profile_skills:
        # "AWS (S3, EC2)" and "AWS" are the same skill listed twice.
        base_name = name.partition("(")[0].strip().lower()
        skill_tokens = set(tokenize(name))
        if not skill_tokens or base_name in seen:
            continue
        seen.add(base_name)
        citing = [alias for alias, words in tokens.items() if skill_tokens <= words]
        skill = LLMSkillOut(name=name, evidence=citing[:2])
        if skill_tokens & requirement_keywords:
            requested.append(skill)
        elif citing:
            mentioned.append(skill)
    return (requested + mentioned)[:MAX_SKILLS]


def _join_names(names: list[str]) -> str:
    """["Python"] -> "Python"; ["Python", "Go", "SQL"] -> "Python, Go and SQL"."""
    if len(names) < 2:
        return "".join(names)
    return f"{', '.join(names[:-1])} and {names[-1]}"


def _summary(skills: list[LLMSkillOut]) -> list[LLMStatement]:
    """One sentence naming the leading skills that have evidence. Each named
    skill occurs in an evidence record the sentence cites."""
    backed = [skill for skill in skills if skill.evidence][:MAX_SUMMARY_SKILLS]
    if not backed:
        return []
    cited = list(dict.fromkeys(skill.evidence[0] for skill in backed))
    names = _join_names([skill.name for skill in backed])
    return [LLMStatement(text=f"Experience with {names}.", evidence=cited, factual=True)]


# ---- Cover letter ------------------------------------------------------------------


def _greeting(ctx: LLMGenerationContext) -> str:
    role = f"the {ctx.job.title} role" if ctx.job.title else "this role"
    employer = f" at {ctx.job.company}" if ctx.job.company else ""
    return f"Dear Hiring Manager, I am writing to apply for {role}{employer}."


def _cover_letter(
    ctx: LLMGenerationContext, entries: list[LLMEntryOut], relevance: dict[str, int]
) -> list[LLMStatement]:
    """Greeting, up to two paragraphs quoting the most relevant bullets, closing.
    Without evidence only the greeting and the closing remain."""
    bullets = [bullet for entry in entries for bullet in entry.bullets]
    bullets.sort(key=lambda bullet: -relevance[bullet.evidence[0]])
    quoted = bullets[:MAX_LETTER_SENTENCES]
    half = (len(quoted) + 1) // 2
    paragraphs = [LLMStatement(text=_greeting(ctx), evidence=[], factual=False)]
    for lead_in, group in (
        ("Highlights from my experience that relate to this role:", quoted[:half]),
        ("Further relevant work:", quoted[half:]),
    ):
        if group:
            sentences = " ".join(_as_sentence(bullet.text) for bullet in group)
            paragraphs.append(
                LLMStatement(
                    text=f"{lead_in} {sentences}",
                    evidence=[bullet.evidence[0] for bullet in group],
                    factual=True,
                )
            )
    paragraphs.append(LLMStatement(text=CLOSING_PARAGRAPH, evidence=[], factual=False))
    return paragraphs


# ---- Coverage ----------------------------------------------------------------------


def _coverage_item(
    requirement: LLMContextRequirement, tokens: dict[str, set[str]]
) -> LLMCoverageOut:
    """Keyword overlap: all keywords found in the evidence -> supported, some ->
    partial, none -> missing. A keyword counts as a whole: "github actions" is
    found when one evidence record contains both words, and the rationale
    names it as the job analysis wrote it. A requirement without keywords
    cannot be rated this way: it is missing when not one of its own words
    occurs in the evidence, and uncertain otherwise."""
    keywords = [keyword for keyword in dict.fromkeys(requirement.keywords) if tokenize(keyword)]
    if not keywords:
        words = set(tokenize(requirement.text))
        if any(words & evidence_words for evidence_words in tokens.values()):
            status = "uncertain"
            rationale = "This requirement has no keywords to match against the profile."
        else:
            status, rationale = "missing", "No evidence was found in the supplied profile."
        return LLMCoverageOut(
            requirement=requirement.alias, status=status, evidence=[], rationale=rationale
        )
    # The evidence retrieved for this requirement is searched (and cited) first.
    candidates = [alias for alias in requirement.candidate_evidence if alias in tokens]
    ordered = candidates + [alias for alias in tokens if alias not in candidates]
    found: list[str] = []
    citing: list[str] = []
    for keyword in keywords:
        words = set(tokenize(keyword))
        alias = next((alias for alias in ordered if words <= tokens[alias]), None)
        if alias is not None:
            found.append(keyword)
            if alias not in citing:
                citing.append(alias)
    absent = [keyword for keyword in keywords if keyword not in found]

    mentions = f"The cited evidence mentions {_join_names(found)}"
    if not found:
        status, rationale = "missing", "No evidence was found in the supplied profile."
    elif absent:
        status, rationale = "partial", f"{mentions} but not {_join_names(absent)}."
    else:
        status, rationale = "supported", f"{mentions}."
    return LLMCoverageOut(
        requirement=requirement.alias,
        status=status,
        evidence=citing,
        rationale=rationale,
    )


# ---- Provider operations -----------------------------------------------------------


def generate_documents(ctx: LLMGenerationContext, feedback: list[str] | None) -> LLMGeneration:
    """Build the whole draft from the context. ``feedback`` is not used: the
    output consists of cited evidence text only, so a correction pass has
    nothing to correct and returns the same draft."""
    tokens = _tokens_by_alias(ctx)
    relevance = _relevance(ctx)
    experience, projects = _entries(ctx, relevance)
    skills = _skills(ctx, tokens)
    return LLMGeneration(
        summary=_summary(skills),
        experience=experience,
        projects=projects,
        skills=skills,
        cover_letter=_cover_letter(ctx, experience + projects, relevance),
        coverage=[_coverage_item(requirement, tokens) for requirement in ctx.requirements],
    )


def regenerate_item(ctx: LLMGenerationContext, target: LLMRegenTarget) -> LLMRegenResult:
    """Rewrite one statement from the evidence it cites.

    The fake offers two wordings of an evidence record: its leading sentences
    and its full text. It returns whichever differs from the current text, so a
    regeneration visibly changes the statement when the evidence is longer
    than one sentence. The style instruction is ignored. A statement that
    cites no known evidence (connective text) is returned unchanged.
    """
    texts = {evidence.alias: evidence.text for evidence in ctx.evidence}
    cited = [alias for alias in target.evidence if alias in texts]
    if not cited:
        return LLMRegenResult(text=target.current_text, evidence=[], factual=False)
    source = texts[cited[0]]
    short = _leading_sentences(source)
    text = short if short != target.current_text else _clean(source)
    return LLMRegenResult(text=text, evidence=[cited[0]], factual=True)


def verify_claims(claims: list[LLMClaimCheck]) -> LLMVerification:
    """Word-overlap stand-in for the semantic verifier: the share of a claim's
    words that occur in its evidence decides the verdict (at least half
    supported, at least a quarter partially supported, otherwise unsupported).
    The fake generator's own sentences, which add a few connecting words to
    evidence text, stay above one half."""
    results = []
    for check in claims:
        claim_words = set(tokenize(check.claim))
        evidence_words = set(tokenize(" ".join(check.evidence_texts)))
        share = len(claim_words & evidence_words) / len(claim_words) if claim_words else 1.0
        if share >= 0.5:
            verdict, reason = "supported", "The evidence contains the claim's wording."
        elif share >= 0.25:
            verdict, reason = "partially_supported", "Part of the claim is not in the evidence."
        else:
            verdict, reason = "unsupported", "Most of the claim is not in the evidence."
        results.append(LLMVerdict(id=check.id, verdict=verdict, reason=reason))
    return LLMVerification(results=results)

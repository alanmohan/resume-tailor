"""Job descriptions and their reviewed requirements (/api/jobs).

The job description is untrusted text. It is stored untouched, sent to the
provider only as data, and everything the provider says about it is checked
here: each requirement's quote is located in the description (a requirement
without a locatable quote is marked as inferred, and so is every duty,
because a duty is not a stated qualification), keywords must occur in the
requirement or its quote, a requirement taken from a line that addresses an AI
system is dropped, repeated requirements are merged and the list is capped.
"""

import logging
from datetime import datetime

from app.config import Settings
from app.errors import InputTooLarge, NotFound, ValidationFailed, VersionConflict
from app.logging_config import log_event
from app.providers.base import AIProvider, LLMJobAnalysis, LLMJobInput, LLMRequirement
from app.ratelimit import QuotaService
from app.repositories import Repositories
from app.schemas.common import new_id, utc_now
from app.schemas.documents import JobDoc
from app.schemas.jobs import (
    Job,
    JobCreateRequest,
    JobList,
    JobPatchRequest,
    Requirement,
    RequirementInput,
    SourceSpan,
)
from app.security import SessionContext
from app.services.sessions import SessionService
from app.services.textutil import (
    NormalizedText,
    fold_text,
    locate_quote,
    looks_like_instruction,
    normalize_whitespace,
    surrounding_lines,
)

logger = logging.getLogger(__name__)

# The text limit of a requirement in PATCH /api/jobs/{id} (app/schemas/jobs.py);
# extracted requirements stay within it so they can always be saved again.
MAX_REQUIREMENT_CHARS = 500
MAX_ROLE_SUMMARY_CHARS = 600
MAX_KEYWORDS = 12
MAX_KEYWORD_CHARS = 60


# ---- Checking the provider's analysis (pure) ---------------------------------------


def _checked_keywords(keywords: list[str], statement: str) -> list[str]:
    """Lower-case, unique keywords that occur in ``statement`` (the requirement
    together with the passage quoted for it). A keyword that is not there was
    made up by the model or taken from another part of the posting."""
    known = fold_text(statement)
    kept: list[str] = []
    for raw in keywords:
        keyword = fold_text(raw)
        if (
            keyword
            and len(keyword) <= MAX_KEYWORD_CHARS
            and keyword in known
            and keyword not in kept
        ):
            kept.append(keyword)
    return kept[:MAX_KEYWORDS]


def _locate_span(
    description: str, normalized: NormalizedText, quote: str | None
) -> SourceSpan | None:
    """The quote's position in the ORIGINAL description, or None if it is not there."""
    span = locate_quote(description, normalized.text, normalized.offsets, quote) if quote else None
    if span is None:
        return None
    start, end = span
    return SourceSpan(start=start, end=end, excerpt=description[start:end])


def _keep_most_important(requirements: list[Requirement], limit: int) -> list[Requirement]:
    """Cut the list to ``limit`` entries without changing their order. When
    something has to go, explicitly required items are kept before preferred
    ones, and stated items before inferred ones."""
    if len(requirements) <= limit:
        return requirements
    by_priority = sorted(
        range(len(requirements)),
        key=lambda i: (requirements[i].importance != "required", requirements[i].inferred, i),
    )
    kept = set(by_priority[:limit])
    return [requirement for i, requirement in enumerate(requirements) if i in kept]


def _is_instruction(text: str, span: SourceSpan | None, description: str) -> bool:
    """Whether a requirement is, or was quoted from a line that is, addressed
    to an AI system instead of describing the job."""
    if looks_like_instruction(text):
        return True
    if span is None:
        return False
    return looks_like_instruction(surrounding_lines(description, span.start, span.end))


def _is_inferred(item: LLMRequirement, span: SourceSpan | None) -> bool:
    """Whether a requirement is shown as implied instead of stated by the employer.

    Only a qualification the posting lists counts as stated. A duty
    ("responsibility") describes the work, not what a candidate must bring, so
    it is inferred even when the model did not flag it: a real model returned
    whole duty lists as stated requirements. Without a passage of the posting
    to point at, nothing can be shown as stated either.
    """
    return item.inferred or item.category == "responsibility" or span is None


def build_requirements(
    analysis: LLMJobAnalysis, description: str, normalized: NormalizedText, limit: int
) -> list[Requirement]:
    """Turn the provider's analysis into stored requirements.

    ``normalized`` is the whitespace-normalised copy of ``description`` that
    the provider read; quotes are located in it and mapped back to offsets in
    the original text.
    """
    by_text: dict[str, Requirement] = {}
    for item in analysis.requirements:
        text = item.text.strip()[:MAX_REQUIREMENT_CHARS].rstrip()
        span = _locate_span(description, normalized, item.quote)
        if not text or _is_instruction(text, span, description):
            continue
        quoted = span.excerpt if span else ""
        keywords = _checked_keywords(item.keywords, f"{text} {quoted}")
        key = fold_text(text).rstrip(".")
        repeated = by_text.get(key)
        if repeated is not None:
            # Stated twice: keep one entry. If either mention is "required",
            # the requirement is required.
            if item.importance == "required":
                repeated.importance = "required"
            repeated.keywords = list(dict.fromkeys(repeated.keywords + keywords))[:MAX_KEYWORDS]
            continue
        by_text[key] = Requirement(
            requirement_id=new_id(),
            text=text,
            category=item.category,
            importance=item.importance,
            inferred=_is_inferred(item, span),
            keywords=keywords,
            source_span=span,
        )
    return _keep_most_important(list(by_text.values()), limit)


def checked_role_summary(summary: str) -> str | None:
    """The provider's role summary, trimmed and bounded; None when it is empty
    or repeats an instruction planted in the posting."""
    cleaned = summary.strip()[:MAX_ROLE_SUMMARY_CHARS].rstrip()
    return None if not cleaned or looks_like_instruction(cleaned) else cleaned


# ---- Applying the user's review (pure) ---------------------------------------------


def _review_requirement(stored: Requirement | None, incoming: RequirementInput) -> Requirement:
    if stored is None:
        return Requirement(
            requirement_id=new_id(),
            text=incoming.text,
            category=incoming.category,
            importance=incoming.importance,
            user_edited=True,
        )
    edited = incoming.model_dump(include={"text", "category", "importance"})
    if stored.model_dump(include=set(edited)) == edited:
        return stored
    # Keywords that the new wording no longer contains would be misleading.
    new_text = fold_text(incoming.text)
    keywords = [keyword for keyword in stored.keywords if keyword in new_text]
    return stored.model_copy(update=edited | {"keywords": keywords, "user_edited": True})


def apply_job_patch(
    stored: JobDoc, body: JobPatchRequest, max_requirements: int, now: datetime
) -> JobDoc:
    """The job after the user's review. Returns ``stored`` itself when nothing
    changed, so saving an unchanged list does not mark existing drafts stale."""
    if len(body.requirements) > max_requirements:
        raise ValidationFailed.for_field(
            "requirements",
            f"At most {max_requirements} requirements are allowed; "
            f"{len(body.requirements)} were sent.",
        )
    by_id = {requirement.requirement_id: requirement for requirement in stored.requirements}
    used: set[str] = set()
    requirements = []
    for position, incoming in enumerate(body.requirements):
        requirement_id = incoming.requirement_id
        if requirement_id is not None and (requirement_id not in by_id or requirement_id in used):
            raise ValidationFailed.for_field(
                f"requirements.{position}.requirement_id",
                "Unknown or repeated requirement_id. Send null for a requirement you added.",
            )
        if requirement_id is not None:
            used.add(requirement_id)
        stored_requirement = by_id.get(requirement_id) if requirement_id else None
        requirements.append(_review_requirement(stored_requirement, incoming))

    # A key that was not sent keeps the stored value; an explicit null clears it.
    changes = {name: getattr(body, name) for name in {"company", "title"} & body.model_fields_set}
    updated = stored.model_copy(update=changes | {"requirements": requirements})
    if updated == stored:
        return stored
    return updated.model_copy(update={"version": stored.version + 1, "updated_at": now})


# ---- Operations --------------------------------------------------------------------


async def _load_job(repos: Repositories, owner_id: str, job_id: str) -> JobDoc:
    """The owner's job, or 404. A job of another session looks exactly like a
    missing one."""
    job = await repos.jobs.get(owner_id, job_id)
    if job is None:
        raise NotFound("This job was not found.")
    return job


async def create_job(
    body: JobCreateRequest,
    *,
    session: SessionContext,
    repos: Repositories,
    provider: AIProvider,
    quotas: QuotaService,
    sessions: SessionService,
    settings: Settings,
) -> Job:
    """POST /api/jobs: analyse a job description into editable requirements."""
    length = len(body.description)
    if length > settings.max_job_chars:
        message = (
            f"The job description is {length:,} characters long; the limit is "
            f"{settings.max_job_chars:,}. Shorten it and try again."
        )
        raise InputTooLarge(message, field_errors=[{"field": "description", "message": message}])
    await quotas.charge_operation(session, "job_analysis")
    await quotas.charge_ai_calls(1)

    normalized = normalize_whitespace(body.description)
    analysis, usage = await provider.analyze_job(
        LLMJobInput(title=body.title, company=body.company, description=normalized.text)
    )
    now = utc_now()
    job = JobDoc(
        job_id=new_id(),
        owner_id=session.owner_id,
        version=1,
        company=body.company,
        title=body.title,
        description=body.description,
        role_summary=checked_role_summary(analysis.role_summary),
        requirements=build_requirements(
            analysis, body.description, normalized, settings.max_requirements
        ),
        created_at=now,
        updated_at=now,
        expires_at=session.expires_at,
    )
    await repos.jobs.insert(session.owner_id, job)
    await sessions.guard_after_write(session)
    log_event(
        logger,
        logging.INFO,
        "job_analyzed",
        requirements=len(job.requirements),
        inferred=sum(1 for requirement in job.requirements if requirement.inferred),
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
    )
    return job.to_api()


async def list_jobs(*, session: SessionContext, repos: Repositories) -> JobList:
    jobs = await repos.jobs.list_for_owner(session.owner_id)
    return JobList(jobs=[job.to_summary() for job in jobs])


async def get_job(job_id: str, *, session: SessionContext, repos: Repositories) -> Job:
    return (await _load_job(repos, session.owner_id, job_id)).to_api()


async def patch_job(
    job_id: str,
    body: JobPatchRequest,
    *,
    session: SessionContext,
    repos: Repositories,
    settings: Settings,
) -> Job:
    """PATCH /api/jobs/{job_id}: save the reviewed requirements with optimistic locking."""
    stored = await _load_job(repos, session.owner_id, job_id)
    if stored.version != body.expected_version:
        raise VersionConflict(details={"current_version": stored.version})
    updated = apply_job_patch(stored, body, settings.max_requirements, utc_now())
    if updated is not stored:
        # Conditional on the version read above; a concurrent edit or a
        # "Clear my data" in between makes this write a no-op.
        saved = await repos.jobs.replace(session.owner_id, updated, stored.version)
        if saved is None:
            raise VersionConflict()
        updated = saved
    return updated.to_api()

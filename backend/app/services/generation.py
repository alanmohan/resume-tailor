"""Tailored resume and cover-letter drafts: generating, editing, revalidating.

How one draft is produced (GenerationService.create):

1. Idempotency: the Idempotency-Key is looked up, then claimed atomically, so
   a repeated request never pays for a second generation.
2. Preconditions: the profile is confirmed and fully indexed with the current
   embedding model; the job belongs to the caller.
3. Retrieval selects a bounded set of evidence (app/services/retrieval.py).
4. The model sees that evidence under short aliases (E1.., P1.., R1..) and
   writes statements that cite aliases. It never sees database IDs.
5. The server maps aliases back (unknown ones are dropped), validates every
   statement (app/services/validation.py) and composes all mandatory metadata
   (contact details, role and degree headers, dates, education, certifications)
   verbatim from the confirmed profile. The model cannot supply any of it.
6. If sentences or bullets are unsupported, the model gets one correction pass
   with the concrete findings (an unsupported skill name alone is simply
   dropped). Statements that are still unsupported afterwards are removed and
   listed under ``omitted_claims``; statements that need review stay, flagged.
   A confirmed role left without bullets then gets its own confirmed bullets,
   so no role is printed as a bare heading.
7. Coverage ratings are checked against the evidence (app/services/coverage.py).

Steps 4 to 6 are pure functions in app/services/drafting.py. This module holds
GenerationService, which talks to the database and the provider.
"""

import asyncio
import logging
from datetime import timedelta

from pymongo.errors import PyMongoError

from app.config import Settings
from app.errors import (
    AppError,
    DatabaseUnavailable,
    GenerationInProgress,
    NotFound,
    ProfileNotConfirmed,
    ProfileNotIndexed,
    QuotaExceeded,
    ValidationFailed,
    VersionConflict,
)
from app.logging_config import log_event
from app.prompts import PROMPT_VERSION
from app.providers.base import (
    AIProvider,
    LLMClaimCheck,
    LLMJobBrief,
    LLMRegenTarget,
    ProviderError,
    ProviderInvalidOutput,
    ProviderTimeout,
    Usage,
)
from app.ratelimit import QuotaService
from app.repositories import Repositories
from app.repositories.generations import RUNNING_STALE_AFTER
from app.schemas.common import new_id, utc_now
from app.schemas.documents import GenerationDoc, JobDoc, ProfileDoc
from app.schemas.generations import (
    Generation,
    GenerationError,
    GenerationList,
    GenerationPatchRequest,
    OmittedClaim,
    StaleReason,
    UsageSummary,
)
from app.security import SessionContext
from app.services.coverage import (
    apply_override,
    build_coverage,
    summarize_coverage,
)
from app.services.drafting import (
    REGENERABLE_SECTIONS,
    UNCITED_EDIT_MESSAGE,
    Draft,
    EvidenceIndex,
    ModelContext,
    briefs_of_evidence,
    briefs_of_profile,
    build_grounding,
    build_model_context,
    check_skill,
    check_statement,
    collect_feedback,
    compose_draft,
    fill_empty_entries,
    is_courtesy,
    listed_skills,
    model_text,
    profile_skills,
    remove_unsupported,
)
from app.services.retrieval import (
    PythonRetriever,
    RetrievalFilters,
    RetrievalResult,
    retrieve_for_job,
)
from app.services.sessions import SessionService
from app.services.validation import (
    ClaimVerdict,
    GroundingContext,
    LocatedClaim,
    iter_claims,
    semantic_findings,
    split_known_ids,
    stricter,
    summarize_validation,
)

logger = logging.getLogger(__name__)

NO_EVIDENCE_WARNING = (
    "No evidence in your confirmed profile was retrieved for this job, so the draft "
    "contains only your confirmed details."
)
CORRECTION_FAILED_WARNING = (
    "The correction pass could not be completed, so unsupported statements were "
    "removed instead of rewritten."
)
VERIFIER_SKIPPED_WARNING = (
    "The optional semantic check could not be run; the deterministic checks still applied."
)
UNEXPECTED_FAILURE_MESSAGE = "The generation failed unexpectedly. Please try again."
GENERATION_TIMEOUT_MESSAGE = "Generating the documents took too long. Please try again."

# One generation makes up to four provider calls (embedding, draft, correction
# pass, optional verifier), each with its own timeout, so together they could
# run for longer than RUNNING_STALE_AFTER. A retry with the same key would then
# take the run over and pay for a second generation while the first is still
# working. The whole run is therefore stopped shortly before that moment.
GENERATION_DEADLINE = RUNNING_STALE_AFTER - timedelta(seconds=30)


# ---- Staleness ---------------------------------------------------------------------


def stale_reasons(
    generation: GenerationDoc, profile: ProfileDoc | None, job: JobDoc | None
) -> list[StaleReason]:
    """Why a draft no longer matches the current data: the profile or the job
    it was generated from has a different version now (or is gone)."""
    reasons: list[StaleReason] = []
    profile_unchanged = (
        profile is not None
        and profile.profile_id == generation.profile_id
        and profile.version == generation.profile_version
    )
    if not profile_unchanged:
        reasons.append("profile_changed")
    if job is None or job.version != generation.job_version:
        reasons.append("job_changed")
    return reasons


def _removal_warnings(draft: Draft, omitted: list[OmittedClaim]) -> list[str]:
    """Draft-level notes about what the server dropped from the model's output."""
    warnings = []
    if draft.ignored_citations:
        warnings.append(
            f"{draft.ignored_citations} citation(s) did not refer to retrieved evidence "
            "and were ignored."
        )
    if omitted:
        warnings.append(
            f"{len(omitted)} statement(s) could not be supported by your profile and were "
            "left out. They are listed under omitted claims."
        )
    return warnings


def _usage_summary(usage: Usage, earlier: UsageSummary | None = None) -> UsageSummary:
    """Usage as stored on the draft, added to what earlier calls consumed."""
    earlier = earlier or UsageSummary()
    return UsageSummary(
        input_tokens=earlier.input_tokens + usage.input_tokens,
        output_tokens=earlier.output_tokens + usage.output_tokens,
        embedding_tokens=earlier.embedding_tokens + usage.embedding_tokens,
        provider_calls=earlier.provider_calls + usage.provider_calls,
    )


def _failure_of(error: Exception) -> GenerationError:
    """The code and message stored on a failed generation. Only messages
    written by this application are stored, never exception text."""
    if isinstance(error, (ProviderError, AppError)):
        return GenerationError(code=error.code, message=error.message)
    if isinstance(error, PyMongoError):
        return GenerationError(
            code=DatabaseUnavailable.code, message=DatabaseUnavailable.default_message
        )
    return GenerationError(code="internal_error", message=UNEXPECTED_FAILURE_MESSAGE)


# ---- The service -------------------------------------------------------------------


class GenerationService:
    def __init__(
        self,
        repos: Repositories,
        provider: AIProvider,
        quotas: QuotaService,
        sessions: SessionService,
        settings: Settings,
    ) -> None:
        self._repos = repos
        self._provider = provider
        self._quotas = quotas
        self._sessions = sessions
        self._settings = settings

    # ---- Reading ---------------------------------------------------------------

    async def get(self, session: SessionContext, generation_id: str) -> Generation:
        generation = await self._repos.generations.get(session.owner_id, generation_id)
        if generation is None:
            raise NotFound("Generation not found.")
        return await self._to_api(session.owner_id, generation)

    async def list_summaries(self, session: SessionContext) -> GenerationList:
        owner_id = session.owner_id
        profile = await self._repos.profiles.get_for_owner(owner_id)
        jobs = {job.job_id: job for job in await self._repos.jobs.list_for_owner(owner_id)}
        return GenerationList(
            generations=[
                generation.to_summary(
                    stale=bool(stale_reasons(generation, profile, jobs.get(generation.job_id)))
                )
                for generation in await self._repos.generations.list_for_owner(owner_id)
            ]
        )

    async def _to_api(self, owner_id: str, generation: GenerationDoc) -> Generation:
        profile = await self._repos.profiles.get_for_owner(owner_id)
        job = await self._repos.jobs.get(owner_id, generation.job_id)
        return generation.to_api(stale_reasons(generation, profile, job))

    # ---- Creating --------------------------------------------------------------

    async def create(
        self, session: SessionContext, job_id: str, idempotency_key: str
    ) -> tuple[Generation, bool]:
        """Generate documents for a job. Returns ``(generation, created)``;
        ``created`` is False when the stored result of an earlier request with
        the same key is returned (no quota charged, no provider call)."""
        owner_id = session.owner_id
        earlier = await self._repos.generations.find_by_idempotency_key(owner_id, idempotency_key)
        if earlier is not None:
            replay = self._replay(earlier, job_id)
            if replay is not None:
                return await self._to_api(owner_id, replay), False

        job = await self._repos.jobs.get(owner_id, job_id)
        if job is None:
            raise NotFound("Job not found.")
        profile, index = await self._load_ready_profile(owner_id)

        # Charged on attempt and before the claim. Two requests that race with
        # the same key are therefore both charged, which is the safe direction.
        await self._quotas.charge_operation(session, "generation")
        await self._quotas.charge_ai_calls(2 if index.documents else 1)

        now = utc_now()
        candidate = GenerationDoc(
            generation_id=new_id(),
            owner_id=owner_id,
            idempotency_key=idempotency_key,
            status="running",
            started_at=now,
            job_id=job.job_id,
            job_version=job.version,
            job_title=job.title,
            company=job.company,
            profile_id=profile.profile_id,
            profile_version=profile.version,
            provider_mode=self._provider.mode,
            model=self._provider.generation_model,
            created_at=now,
            updated_at=now,
            expires_at=session.expires_at,
        )
        running, claimed = await self._repos.generations.claim(owner_id, candidate, now)
        if not claimed:
            if running.status == "completed":
                return await self._to_api(owner_id, running), False
            raise GenerationInProgress(running.generation_id)

        try:
            finished = await self._generate_within_deadline(running, profile, job, index)
            stored = await self._repos.generations.complete(owner_id, finished)
        except Exception as error:
            await self._record_failure(session, running, error)
            raise
        await self._sessions.guard_after_write(session)
        if stored is None:
            # A retry took this run over after it went stale; that run is the
            # one whose result counts.
            raise GenerationInProgress(running.generation_id)
        return await self._to_api(owner_id, stored), True

    @staticmethod
    def _replay(earlier: GenerationDoc, job_id: str) -> GenerationDoc | None:
        """Decide what an already used Idempotency-Key means: return the stored
        draft (completed), raise 409 (still running), or None to run again
        (failed, or running for so long that it was abandoned)."""
        if earlier.job_id != job_id:
            raise ValidationFailed.for_field(
                "job_id", "This Idempotency-Key was already used for a different job."
            )
        if earlier.status == "completed":
            return earlier
        abandoned = earlier.started_at < utc_now() - RUNNING_STALE_AFTER
        if earlier.status == "running" and not abandoned:
            raise GenerationInProgress(earlier.generation_id)
        return None

    async def _load_ready_profile(self, owner_id: str) -> tuple[ProfileDoc, EvidenceIndex]:
        """The profile and its evidence, or 409 if generation must not run:
        not confirmed, edited since confirmation, or not completely indexed
        with the embedding model in use now."""
        profile = await self._repos.profiles.get_for_owner(owner_id)
        if profile is None or profile.status != "confirmed":
            raise ProfileNotConfirmed()
        if profile.index_state != "indexed" or profile.indexed_version != profile.version:
            raise ProfileNotIndexed()
        evidence = await self._repos.evidence.list_for_version(
            owner_id, profile.profile_id, profile.version, with_vectors=False
        )
        for document in evidence:
            current = (
                document.embedding_status == "embedded"
                and document.embedding_model == self._provider.embedding_model
                and document.embedding_dimension == self._provider.embedding_dimension
            )
            if not current:
                raise ProfileNotIndexed(
                    "The profile's evidence is not fully indexed with the current embedding "
                    "model. Confirm the profile again."
                )
        return profile, EvidenceIndex.of(evidence)

    async def _record_failure(
        self, session: SessionContext, running: GenerationDoc, error: Exception
    ) -> None:
        """Mark the claimed run failed so the same key can be retried. If the
        database itself is down this cannot be stored; the record then counts
        as abandoned after RUNNING_STALE_AFTER."""
        failure = _failure_of(error)
        log_event(
            logger,
            logging.WARNING,
            "generation_failed",
            generation_id=running.generation_id,
            attempt=running.attempt,
            code=failure.code,
        )
        try:
            await self._repos.generations.fail(
                session.owner_id, running.generation_id, running.attempt, failure, utc_now()
            )
        except PyMongoError:
            return
        await self._sessions.guard_after_write(session)

    async def _generate_within_deadline(
        self, running: GenerationDoc, profile: ProfileDoc, job: JobDoc, index: EvidenceIndex
    ) -> GenerationDoc:
        """Run the generation, giving up at GENERATION_DEADLINE. The run is then
        recorded as failed with a retryable timeout, so the same
        Idempotency-Key can be used again without two runs overlapping."""
        try:
            async with asyncio.timeout(GENERATION_DEADLINE.total_seconds()):
                return await self._generate(running, profile, job, index)
        except TimeoutError:
            raise ProviderTimeout(GENERATION_TIMEOUT_MESSAGE) from None

    async def _generate(
        self, running: GenerationDoc, profile: ProfileDoc, job: JobDoc, index: EvidenceIndex
    ) -> GenerationDoc:
        """Steps 3 to 7 of the module docstring. Returns the completed document
        (not stored yet)."""
        retrieval = await self._retrieve(running.owner_id, profile, job, index)
        context = build_model_context(
            LLMJobBrief(title=job.title, company=job.company, role_summary=job.role_summary),
            job.requirements,
            briefs_of_profile(profile.records),
            retrieval.evidence,
            retrieval.candidates_by_requirement,
            index,
            contact_name=profile.contact.name,
            skills=profile_skills(profile.records),
        )
        grounding = build_grounding(
            index,
            job.requirements,
            job_title=job.title,
            company=job.company,
            contact_name=profile.contact.name,
        )
        warnings = [] if retrieval.evidence else [NO_EVIDENCE_WARNING]

        output, call_usage = await self._provider.generate_documents(context.llm, None)
        usage = retrieval.usage + call_usage
        draft = compose_draft(output, profile, context, index, grounding)

        corrected = False
        if draft.needs_correction_pass():
            second, call_usage = await self._correction_pass(
                draft, profile, context, index, grounding
            )
            usage += call_usage
            if second is None:
                warnings.append(CORRECTION_FAILED_WARNING)
            elif second.unsupported_count() <= draft.unsupported_count():
                draft, corrected = second, True

        usage += await self._apply_verifier(draft.claims(), index, warnings)
        omitted = remove_unsupported(draft)
        # Evidence the model was not shown but the draft now cites: stored with
        # the retrieved evidence so these bullets can be regenerated later.
        retrieved_ids = list(
            dict.fromkeys(
                [doc.evidence_id for doc in retrieval.evidence]
                + fill_empty_entries(draft, profile, index)
            )
        )
        warnings += _removal_warnings(draft, omitted)
        coverage = build_coverage(
            job.requirements,
            draft.coverage,
            index.searchable,
            job_title=job.title,
            company=job.company,
        )

        now = utc_now()
        log_event(
            logger,
            logging.INFO,
            "generation_completed",
            generation_id=running.generation_id,
            attempt=running.attempt,
            prompt_version=PROMPT_VERSION,
            retrieved=len(retrieval.evidence),
            omitted=len(omitted),
            correction_pass_used=corrected,
            provider_calls=usage.provider_calls,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            embedding_tokens=usage.embedding_tokens,
        )
        return running.model_copy(
            update={
                "status": "completed",
                "error": None,
                "retrieved_evidence_ids": retrieved_ids,
                "resume": draft.resume,
                "cover_letter": draft.cover_letter,
                "coverage": coverage,
                "coverage_summary": summarize_coverage(coverage),
                "validation": summarize_validation(draft.resume, draft.cover_letter, now),
                "omitted_claims": omitted,
                "warnings": warnings,
                "usage": _usage_summary(usage),
                "updated_at": now,
            }
        )

    async def _retrieve(
        self, owner_id: str, profile: ProfileDoc, job: JobDoc, index: EvidenceIndex
    ) -> RetrievalResult:
        if not index.documents:
            # Nothing to rank, so no embedding call is paid for.
            return RetrievalResult(profile.version, [], {}, Usage())
        return await retrieve_for_job(
            PythonRetriever(self._repos.evidence),
            self._provider,
            owner_id,
            profile.version,
            job,
            RetrievalFilters(
                profile_id=profile.profile_id,
                embedding_model=self._provider.embedding_model,
                embedding_dimension=self._provider.embedding_dimension,
            ),
            per_requirement=self._settings.retrieval_per_requirement,
            max_context=self._settings.retrieval_max_context,
            token_budget=self._settings.retrieval_token_budget,
        )

    async def _correction_pass(
        self,
        draft: Draft,
        profile: ProfileDoc,
        context: ModelContext,
        index: EvidenceIndex,
        grounding: GroundingContext,
    ) -> tuple[Draft | None, Usage]:
        """The single allowed regeneration: the model gets the validators'
        findings and writes the draft again. There is no loop; whatever is
        still unsupported afterwards is removed by the caller. A failure here
        (provider error or the daily AI limit) returns None, so the first
        draft, which is already paid for, is kept."""
        try:
            await self._quotas.charge_ai_calls(1)
            output, usage = await self._provider.generate_documents(
                context.llm, collect_feedback(draft)
            )
        except (ProviderError, QuotaExceeded):
            return None, Usage()
        return compose_draft(output, profile, context, index, grounding), usage

    async def _apply_verifier(
        self, claims: list[LocatedClaim], index: EvidenceIndex, warnings: list[str]
    ) -> Usage:
        """Optional second opinion (ENABLE_SEMANTIC_VERIFIER) on statements the
        deterministic checks accepted. It can only make a status stricter. If
        the call fails, the deterministic result stands and a warning is added."""
        if not self._settings.enable_semantic_verifier:
            return Usage()
        accepted = {
            located.claim.item_id: located.claim
            for located in claims
            if located.claim.validation_status == "supported"
            and located.section in REGENERABLE_SECTIONS
        }
        checks = [
            LLMClaimCheck(
                id=item_id,
                claim=claim.text,
                evidence_texts=[
                    model_text(index.documents[evidence_id])
                    for evidence_id in claim.evidence_ids
                    if evidence_id in index.documents
                ],
            )
            for item_id, claim in accepted.items()
        ]
        if not checks:
            return Usage()
        try:
            await self._quotas.charge_ai_calls(1)
            findings, usage = await semantic_findings(self._provider, checks)
        except (ProviderError, QuotaExceeded):
            if VERIFIER_SKIPPED_WARNING not in warnings:
                warnings.append(VERIFIER_SKIPPED_WARNING)
            return Usage()
        for item_id, finding in findings.items():
            claim = accepted[item_id]
            verdict = stricter(ClaimVerdict(claim.validation_status, claim.warnings), finding)
            claim.validation_status, claim.warnings = verdict.status, verdict.warnings
        return usage

    # ---- Editing, revalidating, regenerating one item --------------------------

    async def patch(
        self, session: SessionContext, generation_id: str, body: GenerationPatchRequest
    ) -> Generation:
        """Save manual edits and coverage corrections. Edited statements become
        "user_edited" and lose their old verdict; no text is ever regenerated here."""
        generation = await self._get_completed(session.owner_id, generation_id)
        if generation.revision != body.expected_revision:
            raise VersionConflict(details={"current_revision": generation.revision})
        claims = {
            located.claim.item_id: located.claim
            for located in iter_claims(generation.resume, generation.cover_letter)
        }
        coverage = {item.requirement_id: item for item in generation.coverage}
        unknown = [
            {"field": f"edits.{position}.item_id", "message": "No such item in this draft."}
            for position, edit in enumerate(body.edits)
            if edit.item_id not in claims
        ] + [
            {
                "field": f"coverage_overrides.{position}.requirement_id",
                "message": "No such requirement in this draft.",
            }
            for position, override in enumerate(body.coverage_overrides)
            if override.requirement_id not in coverage
        ]
        if unknown:
            raise ValidationFailed(field_errors=unknown)

        for edit in body.edits:
            claim = claims[edit.item_id]
            if edit.text != claim.text:
                claim.text = edit.text
                claim.validation_status = "user_edited"
                claim.user_edited = True
                claim.warnings = []
        for override in body.coverage_overrides:
            apply_override(coverage[override.requirement_id], override.status, override.note)
        generation.coverage_summary = summarize_coverage(generation.coverage)
        saved = await self._save(session, generation, validated=False)
        return await self._to_api(session.owner_id, saved)

    async def validate(self, session: SessionContext, generation_id: str) -> Generation:
        """Re-check every statement the user edited against the evidence it
        cites. Text is never changed; only statuses and warnings are."""
        owner_id = session.owner_id
        generation = await self._get_completed(owner_id, generation_id)
        await self._quotas.charge_operation(session, "validation")
        index = await self._version_index(owner_id, generation)
        job = await self._repos.jobs.get(owner_id, generation.job_id)
        grounding = self._stored_grounding(generation, job, index)
        # The skills listed in the profile count only while the profile is
        # still the version this draft was generated from.
        profile = await self._repos.profiles.get_for_owner(owner_id)
        unchanged = profile is not None and "profile_changed" not in stale_reasons(
            generation, profile, job
        )
        listed = listed_skills(profile.records) if unchanged else {}

        edited = [
            located
            for located in iter_claims(generation.resume, generation.cover_letter)
            if located.claim.user_edited
        ]
        for located in edited:
            _revalidate(located, index, grounding, listed)
        usage = await self._apply_verifier(edited, index, generation.warnings)
        generation.usage = _usage_summary(usage, generation.usage)
        saved = await self._save(session, generation, validated=True)
        log_event(
            logger,
            logging.INFO,
            "generation_validated",
            generation_id=generation_id,
            revalidated=len(edited),
            needs_review=saved.validation.needs_review_count,
            unsupported=saved.validation.unsupported_count,
        )
        return await self._to_api(owner_id, saved)

    async def regenerate_item(
        self,
        session: SessionContext,
        generation_id: str,
        item_id: str,
        idempotency_key: str,
        instruction: str | None,
    ) -> Generation:
        """Rewrite one statement from the draft's stored evidence. A repeated
        Idempotency-Key returns the stored draft without another provider call."""
        owner_id = session.owner_id
        generation = await self._get_completed(owner_id, generation_id)
        located = _find_claim(generation, item_id)
        if located.section not in REGENERABLE_SECTIONS:
            raise ValidationFailed(
                "This item comes straight from your confirmed profile and cannot be "
                "regenerated. Edit it, or change the profile."
            )
        first_use = await self._repos.generations.claim_regeneration_key(
            owner_id, generation_id, idempotency_key
        )
        if not first_use:
            return await self._to_api(owner_id, generation)
        try:
            await self._quotas.charge_operation(session, "regeneration")
            await self._quotas.charge_ai_calls(1)
            saved = await self._regenerate(session, generation_id, item_id, instruction)
        except Exception:
            # Free the key so the same request can be retried after a failure.
            await self._repos.generations.release_regeneration_key(
                owner_id, generation_id, idempotency_key
            )
            raise
        return await self._to_api(owner_id, saved)

    async def _regenerate(
        self, session: SessionContext, generation_id: str, item_id: str, instruction: str | None
    ) -> GenerationDoc:
        owner_id = session.owner_id
        # Loaded again after the key was claimed, so the document that is saved
        # below already contains the key.
        generation = await self._get_completed(owner_id, generation_id)
        located = _find_claim(generation, item_id)
        claim = located.claim
        index = await self._version_index(owner_id, generation)
        job = await self._repos.jobs.get(owner_id, generation.job_id)
        requirements = job.requirements if job else []
        retrieved = [
            index.documents[evidence_id]
            for evidence_id in generation.retrieved_evidence_ids
            if evidence_id in index.documents
        ]
        context = build_model_context(
            LLMJobBrief(
                title=generation.job_title,
                company=generation.company,
                role_summary=job.role_summary if job else None,
            ),
            requirements,
            briefs_of_evidence(retrieved),
            retrieved,
            {},
            index,
            contact_name=generation.resume.contact.name,
            skills=[skill.text for skill in generation.resume.skills],
        )
        grounding = self._stored_grounding(generation, job, index)
        target = LLMRegenTarget(
            section=located.section,
            current_text=claim.text,
            evidence=context.aliases_of(claim.evidence_ids),
            instruction=instruction,
            feedback=claim.warnings,
        )
        result, usage = await self._provider.regenerate_item(context.llm, target)
        text = result.text.strip()
        if not text:
            raise ProviderInvalidOutput("The AI provider returned an empty statement. Try again.")

        cited, _ = split_known_ids(result.evidence, context.evidence_ids)
        claim.evidence_ids, verdict = check_statement(
            text,
            [context.evidence_ids[alias] for alias in cited],
            index,
            grounding,
            section=located.section,
            record_id=located.record_id,
            factual=result.factual,
        )
        claim.text = text
        claim.validation_status, claim.warnings = verdict.status, verdict.warnings
        claim.user_edited = False
        generation.usage = _usage_summary(usage, generation.usage)
        log_event(
            logger,
            logging.INFO,
            "generation_item_regenerated",
            generation_id=generation_id,
            section=located.section,
            status=verdict.status,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )
        return await self._save(session, generation, validated=False)

    # ---- Shared helpers --------------------------------------------------------

    async def _get_completed(self, owner_id: str, generation_id: str) -> GenerationDoc:
        """The owner's draft, which must have documents to work on."""
        generation = await self._repos.generations.get(owner_id, generation_id)
        if generation is None:
            raise NotFound("Generation not found.")
        if generation.status == "running":
            raise GenerationInProgress(generation.generation_id)
        if generation.status != "completed" or generation.resume is None:
            raise ValidationFailed(
                "This generation did not complete, so it has no documents. Generate again."
            )
        return generation

    async def _version_index(self, owner_id: str, generation: GenerationDoc) -> EvidenceIndex:
        """The evidence of the profile version the draft was generated from.
        Older versions are kept until the session expires, so a stale draft
        can still be validated against what it cites."""
        evidence = await self._repos.evidence.list_for_version(
            owner_id, generation.profile_id, generation.profile_version, with_vectors=False
        )
        return EvidenceIndex.of(evidence)

    @staticmethod
    def _stored_grounding(
        generation: GenerationDoc, job: JobDoc | None, index: EvidenceIndex
    ) -> GroundingContext:
        return build_grounding(
            index,
            job.requirements if job else [],
            job_title=generation.job_title,
            company=generation.company,
            contact_name=generation.resume.contact.name if generation.resume else None,
        )

    async def _save(
        self, session: SessionContext, generation: GenerationDoc, *, validated: bool
    ) -> GenerationDoc:
        """Store a changed draft under optimistic locking, then run the
        post-write guard. The validation summary is recounted from the
        statements; ``validated`` stamps the time of a full revalidation."""
        expected_revision = generation.revision
        now = utc_now()
        generation.revision = expected_revision + 1
        generation.updated_at = now
        generation.validation = summarize_validation(
            generation.resume,
            generation.cover_letter,
            now if validated else generation.validation.validated_at,
        )
        saved = await self._repos.generations.save(session.owner_id, generation, expected_revision)
        await self._sessions.guard_after_write(session)
        if saved is None:
            raise VersionConflict()
        return saved


def _find_claim(generation: GenerationDoc, item_id: str) -> LocatedClaim:
    for located in iter_claims(generation.resume, generation.cover_letter):
        if located.claim.item_id == item_id:
            return located
    raise NotFound("Item not found in this generation.")


def _revalidate(
    located: LocatedClaim,
    index: EvidenceIndex,
    grounding: GroundingContext,
    listed: dict[str, str],
) -> None:
    """Give a user-edited statement a fresh verdict without touching its text.

    A skill is checked for a mention in the profile (``listed`` holds the
    skills the profile lists, see listed_skills). Any other statement is
    checked against the evidence it cites. A cover-letter paragraph without
    citations has nothing to be checked against: it passes as connective text
    only when it is a plain greeting or closing (is_courtesy); anything else
    the user wrote there may state a qualification, so it needs review.
    """
    claim = located.claim
    if located.section == "skills":
        claim.evidence_ids, verdict = check_skill(claim.text, claim.evidence_ids, index, listed)
    else:
        claim.evidence_ids, verdict = check_statement(
            claim.text,
            claim.evidence_ids,
            index,
            grounding,
            section=located.section,
            record_id=located.record_id,
            factual=bool(claim.evidence_ids),
        )
        if verdict.status == "not_applicable" and not is_courtesy(claim.text):
            verdict = ClaimVerdict("needs_review", [UNCITED_EDIT_MESSAGE])
    claim.validation_status, claim.warnings = verdict.status, verdict.warnings

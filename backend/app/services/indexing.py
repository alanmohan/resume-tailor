"""Confirming a profile: evidence records and their embeddings
(POST /api/profile/confirm).

``build_evidence`` turns the reviewed profile into evidence records: one per
semantic unit (a record's overview, its summary, each bullet, each skill
group), not arbitrary slices of text. ``confirm_profile`` stores them, embeds
the ones that have no vector yet and only then marks the profile as indexed.

Evidence IDs are derived from the profile version and the record's position
and text, not drawn at random. Building the evidence of one version twice
therefore yields the same documents, which makes confirming idempotent:

- a retry after a provider failure embeds only what is still missing;
- two confirm requests at once converge on the same documents instead of
  deleting each other's work;
- drafts that cite evidence keep working when a version is confirmed again.
"""

import hashlib
import logging
import re
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from itertools import batched

from pymongo.errors import BulkWriteError, DuplicateKeyError

from app.config import Settings
from app.errors import (
    ProfileNotIndexed,
    QuotaExceeded,
    TooManyChunks,
    UnresolvedConflicts,
    ValidationFailed,
    VersionConflict,
)
from app.logging_config import log_event
from app.providers.base import AIProvider, ProviderError, Usage
from app.ratelimit import QuotaService
from app.repositories import Repositories
from app.schemas.common import Provenance, utc_now
from app.schemas.documents import EvidenceDoc, ProfileDoc, SourceDoc
from app.schemas.evidence import EvidenceParent, EvidenceSource
from app.schemas.profiles import (
    IndexProgress,
    IndexState,
    Profile,
    ProfileConfirmRequest,
    ProfileRecord,
    SourceRef,
)
from app.security import SessionContext
from app.services.ingestion import PreparedSource, prepare_source
from app.services.profile_service import load_profile, version_conflict
from app.services.sessions import SessionService
from app.services.textutil import chunk_text, content_hash, fold_text, strip_contact_details

logger = logging.getLogger(__name__)

CHUNK_MIN_WORDS = 100
CHUNK_MAX_WORDS = 250
# Texts per embedding call. Kept below the provider client's own request size
# (64), so one batch is exactly one provider call and one progress update.
EMBED_BATCH_SIZE = 50

USER_SOURCE_TYPE = "user"
USER_SOURCE_LABELS = {
    "user_edited": "Edited during profile review",
    "user_added": "Added during profile review",
}
UNLOCATED_SOURCE_TYPE = "unknown"
UNLOCATED_SOURCE_LABEL = "Source location not found"

# Shown to the user, so it names no provider detail and no profile text.
INDEX_FAILED_MESSAGE = (
    "Indexing stopped before every part of the profile was embedded. "
    "Your profile is saved; confirm it again to continue."
)


# ---- Building evidence records (pure) ----------------------------------------------


@dataclass(frozen=True)
class _Unit:
    """One statement of the profile before it is chunked."""

    bullet_id: str | None
    # The statement itself; the record context is added in front when embedding.
    body: str
    # The statement as shown in a citation when there is no source excerpt.
    own_words: str
    provenance: Provenance
    # Where the statement is written in a source; None for the user's own
    # statements and for extracted text whose source span was not found.
    ref: SourceRef | None


def _context(record: ProfileRecord) -> str:
    """Short description of the record a statement belongs to. It is put in
    front of every evidence text, so a bullet such as "Cut build time by half"
    still says which role or project it is about once it stands alone."""
    organization = record.organization
    if record.category == "employment":
        return f"{record.title} at {organization}" if organization else record.title
    if record.category == "education":
        return f"{record.title}, {organization}" if organization else record.title
    if record.category == "skill":
        return "Skills" if fold_text(record.title) == "skills" else f"Skills ({record.title})"
    described = f"{record.category.capitalize()}: {record.title}"
    return f"{described} ({organization})" if organization else described


def statement_part(evidence_text: str) -> str:
    """An evidence text without the "[context] " prefix that build_evidence
    puts in front of it: the statement on its own. Empty when the text is
    nothing but the prefix (a record overview without dates or location)."""
    if not evidence_text.startswith("["):
        return evidence_text
    return evidence_text.partition("] ")[2]


def _overview(record: ProfileRecord) -> str:
    """What the record itself states besides its name: the skill list of a
    skill group, otherwise dates and location exactly as confirmed."""
    if record.category == "skill":
        return ", ".join(record.skills)
    dates = " - ".join(date for date in (record.start_date, record.end_date) if date)
    return ", ".join(part for part in (dates, record.location) if part)


def _record_units(
    record: ProfileRecord, context: str, sources: dict[str, PreparedSource]
) -> list[_Unit]:
    """The semantic units of one record: its overview, its summary paragraph
    and each of its statements."""
    units = []
    overview = _overview(record)
    if overview or record.category != "skill":
        # On its own the overview has to name the record: "Analyst at Acme: 2019 - 2021".
        named = f"{context}: {overview}" if overview else context
        units.append(_Unit(None, overview, named, record.provenance, record.source_ref))
    if record.summary:
        # The profile stores no reference for the summary paragraph, so it is
        # looked up in the record's source, starting at the record's header.
        header = record.source_ref if record.provenance == "extracted" else None
        source = sources.get(header.source_id) if header else None
        ref = source.locate(record.summary, header.start) if source and header else None
        units.append(_Unit(None, record.summary, record.summary, record.provenance, ref))
    units.extend(
        _Unit(bullet.bullet_id, bullet.text, bullet.text, bullet.provenance, bullet.source_ref)
        for bullet in record.bullets
    )
    return units


def _cite(
    unit: _Unit, chunk: str, is_whole_unit: bool, sources: dict[str, PreparedSource]
) -> tuple[EvidenceSource, str, int | None]:
    """``(source, excerpt, source_revision)`` shown when a citation is opened.

    The user's own statements cite no source: the excerpt is their wording.
    Extracted text cites its span in the original source; one chunk of a long
    statement is located inside that statement.
    """
    own_words = unit.own_words if is_whole_unit else chunk
    if unit.provenance != "extracted":
        label = USER_SOURCE_LABELS[unit.provenance]
        return EvidenceSource(label=label, source_type=USER_SOURCE_TYPE), own_words, None
    source = sources.get(unit.ref.source_id) if unit.ref else None
    if unit.ref is None or source is None:
        unlocated = EvidenceSource(label=UNLOCATED_SOURCE_LABEL, source_type=UNLOCATED_SOURCE_TYPE)
        return unlocated, own_words, None
    ref = unit.ref if is_whole_unit else source.locate(chunk, unit.ref.start) or unit.ref
    cited = EvidenceSource(
        source_id=ref.source_id,
        label=source.doc.label,
        source_type=source.doc.source_type,
        start=ref.start,
        end=ref.end,
    )
    return cited, ref.excerpt, source.doc.revision


def _skill_patterns(records: list[ProfileRecord]) -> dict[str, re.Pattern[str]]:
    """``{tag: pattern}`` for every skill in the profile. A skill written with
    a note in brackets, such as "AWS (S3, EC2)", is also known by its name."""
    patterns: dict[str, re.Pattern[str]] = {}
    for record in records:
        for skill in record.skills:
            for term in (fold_text(skill), fold_text(skill.partition("(")[0])):
                if term:
                    patterns[term] = re.compile(rf"(?<![\w+#]){re.escape(term)}(?![\w+#])")
    return patterns


def _chunks(unit: _Unit) -> list[str]:
    """The unit's statement split into chunks of roughly 100-250 words. An
    overview without dates or location has nothing to split, but it still
    states that the role or project exists, so it yields one empty chunk."""
    chunks = chunk_text(unit.body, CHUNK_MIN_WORDS, CHUNK_MAX_WORDS)
    return [chunk.text for chunk in chunks] or [""]


def _evidence_id(profile: ProfileDoc, position: int, text_hash: str) -> str:
    """Deterministic ID: the same profile version always produces the same
    evidence IDs (see the module docstring). The owner ID is random and secret,
    so the IDs cannot be guessed by another visitor."""
    key = f"{profile.owner_id}:{profile.profile_id}:{profile.version}:{position}:{text_hash}"
    return hashlib.sha256(key.encode()).hexdigest()[:32]


def build_evidence(
    profile: ProfileDoc,
    source_docs: list[SourceDoc],
    *,
    embedding_model: str,
    embedding_dimension: int,
    now: datetime,
) -> list[EvidenceDoc]:
    """The evidence records of this profile version, without vectors.

    Each record keeps the original excerpt for display and a normalised
    ``text`` for embedding: the record context in square brackets, then the
    statement with e-mail addresses, phone numbers and URLs removed. Long
    statements are split at paragraph or sentence boundaries.
    """
    sources = {doc.source_id: prepare_source(doc.source_id, doc) for doc in source_docs}
    skill_patterns = _skill_patterns(profile.records)
    evidence: list[EvidenceDoc] = []

    for record in profile.records:
        context = _context(record)
        parent = EvidenceParent(
            record_id=record.record_id,
            category=record.category,
            title=record.title,
            organization=record.organization,
        )
        for unit in _record_units(record, context, sources):
            chunks = _chunks(unit)
            for chunk in chunks:
                statement = " ".join(strip_contact_details(chunk).split())
                if chunk and not statement:
                    continue  # nothing but contact details: no evidence in it
                # statement_part() takes this prefix off again.
                text = f"[{context}] {statement}".rstrip()
                source, excerpt, revision = _cite(unit, chunk, len(chunks) == 1, sources)
                # Tags come from the statement only; the context would tag every
                # bullet of a "Machine Learning Engineer" with machine learning.
                folded = fold_text(statement)
                evidence.append(
                    EvidenceDoc(
                        evidence_id=_evidence_id(profile, len(evidence), content_hash(text)),
                        owner_id=profile.owner_id,
                        profile_id=profile.profile_id,
                        profile_version=profile.version,
                        position=len(evidence),
                        record_id=record.record_id,
                        bullet_id=unit.bullet_id,
                        source=source,
                        source_revision=revision,
                        provenance=unit.provenance,
                        excerpt=excerpt,
                        text=text,
                        category=record.category,
                        tags=[tag for tag, term in skill_patterns.items() if term.search(folded)],
                        parent=parent,
                        embedding_model=embedding_model,
                        embedding_dimension=embedding_dimension,
                        content_hash=content_hash(text),
                        created_at=now,
                        expires_at=profile.expires_at,
                    )
                )
    return evidence


# ---- Storing and embedding ---------------------------------------------------------


def _is_unusable(stored: list[EvidenceDoc], expected: list[EvidenceDoc]) -> bool:
    """True when documents stored for this version cannot be built upon: they
    were embedded with another model or dimension, or are not part of the
    expected set. Vectors of different models must never be mixed."""
    by_id = {doc.evidence_id: doc for doc in expected}
    return any(
        doc.evidence_id not in by_id
        or doc.embedding_model != by_id[doc.evidence_id].embedding_model
        or doc.embedding_dimension != by_id[doc.evidence_id].embedding_dimension
        for doc in stored
    )


class _IndexRun:
    """One attempt to bring a profile version to "confirmed and indexed".

    The steps are idempotent, so the attempt may be repeated after a failure
    or run twice at the same time: documents that are already stored are kept,
    and only documents without a vector are embedded.
    """

    def __init__(
        self,
        profile: ProfileDoc,
        expected: list[EvidenceDoc],
        repos: Repositories,
        provider: AIProvider,
        quotas: QuotaService,
    ) -> None:
        self.profile = profile
        self.expected = expected
        self.repos = repos
        self.provider = provider
        self.quotas = quotas
        self.owner_id = profile.owner_id

    async def _set_state(
        self, state: IndexState, embedded: int, error: str | None = None
    ) -> ProfileDoc | None:
        """Record progress on the profile. Returns None if the profile was
        edited or deleted meanwhile (the update only applies to this version)."""
        version = self.profile.version
        confirmed = (
            {"indexed_version": version, "status": "confirmed"} if state == "indexed" else {}
        )
        return await self.repos.profiles.set_index_state(
            self.owner_id,
            version,
            index_state=state,
            index_progress=IndexProgress(total=len(self.expected), embedded=embedded),
            now=utc_now(),
            index_error=error,
            **confirmed,
        )

    async def _insert(self, documents: list[EvidenceDoc]) -> None:
        try:
            await self.repos.evidence.insert_many(self.owner_id, documents)
        except BulkWriteError:
            # A concurrent confirm of the same version stored some of these IDs
            # first. IDs are derived from the content, so its documents are the
            # same as ours: add only the ones that are still missing.
            for document in documents:
                with suppress(DuplicateKeyError):
                    await self.repos.evidence.insert(self.owner_id, document)

    async def _store_missing(self) -> list[EvidenceDoc]:
        """Make sure every expected evidence document is stored and return the
        ones that still need a vector."""
        profile, evidence = self.profile, self.repos.evidence
        stored = await evidence.list_for_version(
            self.owner_id, profile.profile_id, profile.version, with_vectors=False
        )
        if _is_unusable(stored, self.expected):
            await self._set_state("indexing", 0)
            await evidence.delete_version(self.owner_id, profile.profile_id, profile.version)
            stored = []
        stored_ids = {doc.evidence_id for doc in stored}
        embedded_ids = {doc.evidence_id for doc in stored if doc.embedding_status == "embedded"}
        missing = [doc for doc in self.expected if doc.evidence_id not in stored_ids]

        # Embedding cache: reuse the vector of any evidence this owner already
        # has with the same text, model and dimension (for example from the
        # previous profile version), so an unchanged statement is paid for once.
        cached = await evidence.find_reusable_embeddings(
            self.owner_id,
            [doc.content_hash for doc in missing],
            self.provider.embedding_model,
            self.provider.embedding_dimension,
        )
        for doc in missing:
            if doc.content_hash in cached:
                doc.embedding = cached[doc.content_hash]
                doc.embedding_status = "embedded"
                embedded_ids.add(doc.evidence_id)
        pending = [doc for doc in self.expected if doc.evidence_id not in embedded_ids]

        if missing or pending:
            # From here until the final update the profile is not "indexed", so
            # nothing can be generated from a partly embedded version.
            await self._set_state("indexing", len(self.expected) - len(pending))
        await self._insert(missing)
        return pending

    async def _embed(self, pending: list[EvidenceDoc]) -> Usage:
        """Embed ``pending`` in batches, storing vectors and progress after each
        batch. On a provider failure everything stored so far is kept, the
        profile is marked failed and the error is passed on."""
        embedded = len(self.expected) - len(pending)
        usage = Usage()
        for batch in batched(pending, EMBED_BATCH_SIZE):
            batch_ids = [doc.evidence_id for doc in batch]
            try:
                await self.quotas.charge_ai_calls(1)
                vectors, batch_usage = await self.provider.embed([doc.text for doc in batch])
            except (ProviderError, QuotaExceeded):
                await self.repos.evidence.mark_failed(self.owner_id, batch_ids)
                await self._set_state("failed", embedded, INDEX_FAILED_MESSAGE)
                raise
            await self.repos.evidence.set_embeddings(
                self.owner_id, dict(zip(batch_ids, vectors, strict=True))
            )
            embedded += len(batch)
            usage += batch_usage
            await self._set_state("indexing", embedded)
        return usage

    async def run(self) -> ProfileDoc:
        profile, total = self.profile, len(self.expected)
        pending = await self._store_missing()
        usage = await self._embed(pending)

        # Trust the database, not the bookkeeping above: the profile is only
        # marked indexed if every evidence document of this version has a vector.
        counts = await self.repos.evidence.count_by_status(
            self.owner_id, profile.profile_id, profile.version
        )
        if counts != {"pending": 0, "embedded": total, "failed": 0}:
            await self._set_state("failed", counts["embedded"], INDEX_FAILED_MESSAGE)
            raise ProfileNotIndexed("Indexing did not complete. Confirm the profile again.")
        confirmed = await self._set_state("indexed", total)
        if confirmed is None:
            raise VersionConflict(
                "The profile changed while it was being indexed. Review it and confirm again."
            )
        log_event(
            logger,
            logging.INFO,
            "profile_indexed",
            chunks=total,
            embedded_now=len(pending),
            embedding_model=self.provider.embedding_model,
            embedding_tokens=usage.embedding_tokens,
            provider_calls=usage.provider_calls,
        )
        return confirmed


async def confirm_profile(
    body: ProfileConfirmRequest,
    *,
    session: SessionContext,
    repos: Repositories,
    provider: AIProvider,
    quotas: QuotaService,
    sessions: SessionService,
    settings: Settings,
) -> Profile:
    """POST /api/profile/confirm: the user accepts the reviewed profile; build
    and embed its evidence. On success the profile is confirmed and indexed at
    exactly this version, which is what generation requires."""
    profile = await load_profile(repos, session.owner_id)
    if profile.version != body.expected_version:
        raise version_conflict(profile.version)
    unresolved = profile.review_summary().unresolved_conflict_count
    if unresolved:
        raise UnresolvedConflicts(details={"unresolved_conflict_count": unresolved})

    source_docs = await repos.sources.list_for_owner(session.owner_id)
    expected = build_evidence(
        profile,
        source_docs,
        embedding_model=provider.embedding_model,
        embedding_dimension=provider.embedding_dimension,
        now=utc_now(),
    )
    if not expected:
        raise ValidationFailed.for_field(
            "records", "The profile is empty. Add at least one record before confirming."
        )
    if len(expected) > settings.max_evidence_chunks:
        raise TooManyChunks(
            f"The profile would be split into {len(expected)} evidence chunks; the limit is "
            f"{settings.max_evidence_chunks}. Remove or shorten some records and confirm again.",
            details={"chunks": len(expected), "limit": settings.max_evidence_chunks},
        )

    await quotas.charge_operation(session, "confirm")
    try:
        confirmed = await _IndexRun(profile, expected, repos, provider, quotas).run()
    finally:
        # Also on failure: evidence written before the error must not survive
        # a "Clear my data" that happened while this request was running.
        await sessions.guard_after_write(session)
    return confirmed.to_api(source_docs)

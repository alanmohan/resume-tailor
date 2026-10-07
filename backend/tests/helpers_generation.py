"""Test helpers for retrieval, generation, validation and coverage tests.

- ``seed_profile`` / ``seed_job`` store a confirmed, fully indexed fictional
  profile and an analysed job directly through the repositories, so these
  tests do not depend on the ingestion and job-analysis routes.
- ``ScriptedProvider`` is the fake provider with one addition: tests can queue
  the exact output the "model" returns, to prove that the server rejects
  adversarial output (invented skills, reused numbers, forged citations).
- ``StubResponsesSDK`` stands in for the OpenAI SDK so the request built by
  the OpenAI operations can be inspected without a network call.

All people, employers and figures here are invented.
"""

import asyncio
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import httpx
from fastapi import FastAPI

from app.db import OWNED_COLLECTIONS, Database
from app.providers.base import (
    LLMBulletOut,
    LLMCoverageOut,
    LLMEntryOut,
    LLMGeneration,
    LLMGenerationContext,
    LLMRegenResult,
    LLMRegenTarget,
    LLMSkillOut,
    LLMStatement,
    Usage,
)
from app.providers.factory import get_provider
from app.providers.fake.embeddings import (
    FAKE_EMBEDDING_DIMENSION,
    FAKE_EMBEDDING_MODEL,
    fake_embedding,
)
from app.providers.fake.provider import FakeProvider
from app.providers.openai.client import OpenAIClient
from app.repositories import Repositories
from app.schemas.documents import EvidenceDoc, JobDoc, ProfileDoc
from app.schemas.evidence import EvidenceParent, EvidenceSource
from app.schemas.jobs import Requirement
from app.schemas.profiles import Contact, IndexProgress, ProfileBullet, ProfileRecord
from app.services.textutil import content_hash
from tests.conftest import SessionHandle
from tests.factories import make_evidence, make_job, make_profile

# ---- A fictional confirmed profile -------------------------------------------------

CONTACT = Contact(
    name="Jordan Rivera",
    email="jordan.rivera@example.com",
    phone="555-0142",
    location="Pittsburgh, PA",
    links=["https://example.com/jordan"],
)


def _bullet(bullet_id: str, text: str) -> ProfileBullet:
    return ProfileBullet(bullet_id=bullet_id, text=text, provenance="extracted")


def sample_records() -> list[ProfileRecord]:
    """Two roles, a personal project, a degree, a certification and a skill
    group. The profile mentions Docker but never Kubernetes, and its only
    "20%" is about cloud costs."""
    return [
        ProfileRecord(
            record_id="role-quill",
            category="employment",
            title="Software Engineer",
            organization="Quillfeather Software",
            location="Pittsburgh, PA",
            start_date="Jul 2022",
            end_date="Jul 2024",
            provenance="extracted",
            bullets=[
                _bullet("b-docker", "Containerised twelve services with Docker for deployment."),
                _bullet("b-costs", "Reduced cloud costs by 20% by rightsizing instances."),
                _bullet("b-latency", "Improved API latency by adding Redis caching."),
            ],
        ),
        ProfileRecord(
            record_id="role-bright",
            category="employment",
            title="Data Engineering Intern",
            organization="Brightloom Labs",
            start_date="May 2021",
            end_date="Aug 2021",
            provenance="extracted",
            bullets=[
                _bullet("b-pipelines", "Built Python data pipelines for 5,000 daily records."),
            ],
        ),
        ProfileRecord(
            record_id="project-trail",
            category="project",
            title="TrailNotes",
            organization="Personal project",
            start_date="Jan 2024",
            end_date="Apr 2024",
            provenance="extracted",
            bullets=[_bullet("b-trail", "Built a hiking journal app with React and PostgreSQL.")],
        ),
        ProfileRecord(
            record_id="edu-fairhaven",
            category="education",
            title="B.S. in Computer Science",
            organization="Fairhaven Institute of Technology",
            start_date="Aug 2018",
            end_date="May 2022",
            provenance="extracted",
            bullets=[_bullet("b-honours", "Graduated with honours.")],
        ),
        ProfileRecord(
            record_id="cert-cloud",
            category="certification",
            title="Cloud Practitioner Certificate",
            organization="Nimbus Training",
            start_date="Mar 2024",
            provenance="extracted",
        ),
        ProfileRecord(
            record_id="skills-main",
            category="skill",
            title="Skills",
            skills=["Python", "Docker", "PostgreSQL", "React", "Redis"],
            provenance="extracted",
        ),
    ]


def _record_context(record: ProfileRecord) -> str:
    """The context the indexer puts in front of every evidence text of a record."""
    if record.category == "skill":
        return record.title
    return " at ".join(part for part in (record.title, record.organization) if part)


def _evidence_units(record: ProfileRecord) -> list[tuple[str | None, str, str]]:
    """``(bullet_id, original wording, statement)`` for every evidence record
    of a profile record, following the indexer's conventions: first one record
    for the profile record itself (its header line as wording; its dates and
    location, or its skill list, as statement), then one per bullet."""
    dates = " - ".join(date for date in (record.start_date, record.end_date) if date)
    if record.category == "skill":
        skills = ", ".join(record.skills)
        units = [(None, f"{record.title}: {skills}", skills)]
    else:
        header = " - ".join(part for part in (record.title, record.organization) if part)
        details = ", ".join(part for part in (dates, record.location) if part)
        units = [(None, f"{header} ({dates})" if dates else header, details)]
    if record.summary:
        units.append((None, record.summary, record.summary))
    units += [(bullet.bullet_id, bullet.text, bullet.text) for bullet in record.bullets]
    return units


def seeded_evidence_id(profile: ProfileDoc, name: str) -> str:
    """Readable evidence ID, unique per owner and profile version. ``name`` is
    a bullet ID ("b-docker") or a record ID ("skills-main") for the evidence
    record of the profile record itself."""
    return f"ev-{profile.owner_id[:8]}-v{profile.version}-{name}"


def evidence_for_profile(profile: ProfileDoc) -> list[EvidenceDoc]:
    """Evidence records as the indexer builds them, embedded with the fake
    embedding model: text is "[record context] statement", the excerpt is the
    original wording, tags are the profile's skills found in the statement."""
    all_skills = {skill.lower() for record in profile.records for skill in record.skills}
    documents: list[EvidenceDoc] = []
    for record in profile.records:
        for number, (bullet_id, excerpt, statement) in enumerate(_evidence_units(record)):
            name = bullet_id or (record.record_id if number == 0 else f"{record.record_id}-summary")
            text = f"[{_record_context(record)}] {statement}".rstrip()
            documents.append(
                make_evidence(
                    profile.owner_id,
                    profile.profile_id,
                    profile.version,
                    evidence_id=seeded_evidence_id(profile, name),
                    position=len(documents),
                    record_id=record.record_id,
                    bullet_id=bullet_id,
                    source=EvidenceSource(
                        source_id="source-1", label="Resume", source_type="resume"
                    ),
                    excerpt=excerpt,
                    text=text,
                    category=record.category,
                    tags=sorted(skill for skill in all_skills if skill in statement.lower()),
                    parent=EvidenceParent(
                        record_id=record.record_id,
                        category=record.category,
                        title=record.title,
                        organization=record.organization,
                    ),
                    embedding_model=FAKE_EMBEDDING_MODEL,
                    embedding_dimension=FAKE_EMBEDDING_DIMENSION,
                    content_hash=content_hash(text),
                    embedding=fake_embedding(text),
                    embedding_status="embedded",
                    expires_at=profile.expires_at,
                )
            )
    return documents


@dataclass
class SeededProfile:
    profile: ProfileDoc
    evidence: list[EvidenceDoc]

    def evidence_id(self, name: str) -> str:
        """ID of the evidence built from a bullet ("b-docker") or record ("skills-main")."""
        return seeded_evidence_id(self.profile, name)


async def seed_profile(
    repos: Repositories,
    session: SessionHandle,
    *,
    records: list[ProfileRecord] | None = None,
    **profile_overrides: Any,
) -> SeededProfile:
    """Store a confirmed profile whose every evidence record is embedded."""
    values: dict[str, Any] = {
        "version": 1,
        "status": "confirmed",
        "index_state": "indexed",
        "contact": CONTACT,
        "records": sample_records() if records is None else records,
        "expires_at": session.expires_at,
    }
    values.update(profile_overrides)
    values.setdefault("indexed_version", values["version"])
    profile = make_profile(session.owner_id, **values)
    evidence = evidence_for_profile(profile)
    profile.index_progress = IndexProgress(total=len(evidence), embedded=len(evidence))
    assert await repos.profiles.create(session.owner_id, profile)
    await repos.evidence.insert_many(session.owner_id, evidence)
    return SeededProfile(profile=profile, evidence=evidence)


def requirement(requirement_id: str, text: str, *keywords: str, **overrides: Any) -> Requirement:
    values: dict[str, Any] = {
        "requirement_id": requirement_id,
        "text": text,
        "category": "skill",
        "importance": "required",
        "keywords": list(keywords),
    }
    values.update(overrides)
    return Requirement(**values)


def sample_requirements() -> list[Requirement]:
    """Python and Docker are in the sample profile; Kubernetes is not."""
    return [
        requirement("req-python", "Python experience", "python"),
        requirement("req-docker", "Experience with Docker", "docker"),
        requirement("req-k8s", "Kubernetes experience", "kubernetes"),
    ]


async def seed_job(
    repos: Repositories,
    session: SessionHandle,
    *,
    requirements: list[Requirement] | None = None,
    **overrides: Any,
) -> JobDoc:
    values: dict[str, Any] = {
        "title": "Platform Engineer",
        "company": "Globex",
        "description": "Globex is hiring a Platform Engineer. Python, Docker and Kubernetes.",
        "role_summary": "Build and run the container platform with Python and Docker.",
        "requirements": sample_requirements() if requirements is None else requirements,
        "expires_at": session.expires_at,
    }
    values.update(overrides)
    job = make_job(session.owner_id, **values)
    await repos.jobs.insert(session.owner_id, job)
    return job


# ---- Calling the generation routes --------------------------------------------------


def with_key(session: SessionHandle, key: str) -> dict[str, str]:
    """The session's headers plus an Idempotency-Key."""
    return {**session.headers, "Idempotency-Key": key}


async def post_generation(
    client: httpx.AsyncClient, session: SessionHandle, job: JobDoc, key: str = "generation-key-1"
) -> httpx.Response:
    return await client.post(
        "/api/generations", json={"job_id": job.job_id}, headers=with_key(session, key)
    )


PROVIDER_CALL_DEADLINE_SECONDS = 5.0


async def wait_for_call(provider: FakeProvider, operation: str) -> None:
    """Return once the provider has started a call of ``operation``. Lets a
    test act while a slow generation is in flight without guessing how long
    the request takes to get that far."""
    async with asyncio.timeout(PROVIDER_CALL_DEADLINE_SECONDS):
        # The fake provider exposes a call counter, not an event, so it is polled.
        while provider.calls[operation] == 0:  # noqa: ASYNC110
            await asyncio.sleep(0.01)


async def owned_counts(db: Database, owner_id: str) -> dict[str, int]:
    """Number of the owner's documents in each owned collection."""
    return {
        name: await db[name].count_documents({"owner_id": owner_id}) for name in OWNED_COLLECTIONS
    }


# ---- Scripting what the "model" returns --------------------------------------------

GenerationScript = LLMGeneration | Callable[[LLMGenerationContext], LLMGeneration]
RegenScript = LLMRegenResult | Callable[[LLMGenerationContext, LLMRegenTarget], LLMRegenResult]


class ScriptedProvider(FakeProvider):
    """FakeProvider whose generation output can be scripted.

    ``queue_generation(a, b)`` makes the next two generate_documents calls
    return ``a`` then ``b``; ``always_generate(a)`` makes every call return
    ``a``. A script is either a ready LLMGeneration or a function of the
    context (to cite the real aliases). Without a script the deterministic
    fake output is returned. Call counting, delays and failure injection keep
    working, and every context and feedback list is recorded.
    """

    def __init__(self) -> None:
        super().__init__()
        self._queued: deque[GenerationScript] = deque()
        self._always: GenerationScript | None = None
        self._regen: deque[RegenScript] = deque()
        self.contexts: list[LLMGenerationContext] = []
        self.feedback: list[list[str] | None] = []
        self.regen_targets: list[LLMRegenTarget] = []

    def queue_generation(self, *scripts: GenerationScript) -> None:
        self._queued.extend(scripts)

    def always_generate(self, script: GenerationScript) -> None:
        self._always = script

    def queue_regeneration(self, *scripts: RegenScript) -> None:
        self._regen.extend(scripts)

    async def generate_documents(
        self, ctx: LLMGenerationContext, feedback: list[str] | None
    ) -> tuple[LLMGeneration, Usage]:
        self.contexts.append(ctx)
        self.feedback.append(feedback)
        script = self._queued.popleft() if self._queued else self._always
        if script is None:
            return await super().generate_documents(ctx, feedback)
        await self._begin("generate_documents")
        output = script(ctx) if callable(script) else script
        return output, Usage(input_tokens=10, output_tokens=10, provider_calls=1)

    async def regenerate_item(
        self, ctx: LLMGenerationContext, target: LLMRegenTarget
    ) -> tuple[LLMRegenResult, Usage]:
        self.contexts.append(ctx)
        self.regen_targets.append(target)
        if not self._regen:
            return await super().regenerate_item(ctx, target)
        await self._begin("regenerate_item")
        script = self._regen.popleft()
        output = script(ctx, target) if callable(script) else script
        return output, Usage(input_tokens=5, output_tokens=5, provider_calls=1)


def install_scripted_provider(app: FastAPI) -> ScriptedProvider:
    """Make ``app`` use a ScriptedProvider for every request."""
    provider = ScriptedProvider()
    app.dependency_overrides[get_provider] = lambda: provider
    return provider


def llm_generation(
    *,
    summary: list[LLMStatement] | None = None,
    experience: list[LLMEntryOut] | None = None,
    projects: list[LLMEntryOut] | None = None,
    skills: list[LLMSkillOut] | None = None,
    cover_letter: list[LLMStatement] | None = None,
    coverage: list[LLMCoverageOut] | None = None,
) -> LLMGeneration:
    """An LLMGeneration with empty lists for everything not given."""
    return LLMGeneration(
        summary=summary or [],
        experience=experience or [],
        projects=projects or [],
        skills=skills or [],
        cover_letter=cover_letter or [],
        coverage=coverage or [],
    )


def llm_statement(text: str, *evidence: str, factual: bool = True) -> LLMStatement:
    return LLMStatement(text=text, evidence=list(evidence), factual=factual)


def llm_entry(record: str, *bullets: tuple[str, list[str]]) -> LLMEntryOut:
    """An entry for the record alias with ``(text, cited aliases)`` bullets."""
    return LLMEntryOut(
        record=record, bullets=[LLMBulletOut(text=text, evidence=cited) for text, cited in bullets]
    )


def evidence_alias(ctx: LLMGenerationContext, fragment: str) -> str:
    """Alias of the one context evidence whose text contains ``fragment``."""
    matches = [item.alias for item in ctx.evidence if fragment in item.text]
    assert len(matches) == 1, f"{fragment!r} matches {matches}"
    return matches[0]


def record_alias(ctx: LLMGenerationContext, title: str) -> str:
    matches = [record.alias for record in ctx.records if record.title == title]
    assert len(matches) == 1, f"{title!r} matches {matches}"
    return matches[0]


def requirement_alias(ctx: LLMGenerationContext, fragment: str) -> str:
    matches = [item.alias for item in ctx.requirements if fragment in item.text]
    assert len(matches) == 1, f"{fragment!r} matches {matches}"
    return matches[0]


# ---- Reading a Generation response -------------------------------------------------


def all_claims(generation: dict[str, Any]) -> list[dict[str, Any]]:
    """Every statement of a Generation response body, in reading order."""
    resume = generation["resume"]
    claims = list(resume["summary"])
    for section in ("experience", "projects", "education", "certifications"):
        for entry in resume[section]:
            claims.extend(entry["bullets"])
    claims.extend(resume["skills"])
    claims.extend(generation["cover_letter"]["paragraphs"])
    return claims


def all_text(generation: dict[str, Any]) -> str:
    """All statement text of a response body, for "this must not appear" checks."""
    return "\n".join(claim["text"] for claim in all_claims(generation))


def entry_for(generation: dict[str, Any], section: str, record_id: str) -> dict[str, Any]:
    matches = [entry for entry in generation["resume"][section] if entry["record_id"] == record_id]
    assert len(matches) == 1, f"{record_id!r} in {section}: {len(matches)} entries"
    return matches[0]


# ---- Inspecting the request the OpenAI operations build ----------------------------


class StubResponsesSDK:
    """Stands in for AsyncOpenAI: records each Responses request and answers
    with the queued parsed outputs."""

    def __init__(self, *outputs: Any) -> None:
        self.outputs = list(outputs)
        self.requests: list[dict[str, Any]] = []
        self.responses = SimpleNamespace(parse=self._parse)

    async def _parse(self, **request: Any) -> Any:
        self.requests.append(request)
        return SimpleNamespace(
            status="completed",
            output=[SimpleNamespace(type="message", content=[SimpleNamespace(type="output_text")])],
            output_parsed=self.outputs.pop(0),
            usage=SimpleNamespace(input_tokens=200, output_tokens=80),
        )

    async def close(self) -> None:
        """Nothing to release."""


def openai_client_with(sdk: StubResponsesSDK) -> OpenAIClient:
    """A real OpenAIClient that talks to the stub instead of the network."""
    return OpenAIClient(
        api_key=None,
        model="gpt-6-luna",
        embedding_model="text-embedding-3-small",
        embedding_dimension=1536,
        reasoning_effort="low",
        timeout_seconds=5,
        max_retries=0,
        sdk_client=sdk,
    )

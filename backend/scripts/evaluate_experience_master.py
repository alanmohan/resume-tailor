"""Local evaluation of the real experience master file against live job postings.

IMPLEMENTATION_SPEC.md, section 9, asks for a local test of the author's own
experience master file against job descriptions retrieved from public careers
pages. This script is that test. Run it from ``backend/`` (it calls the real AI
provider and costs money; see scripts/README.md):

    EXPERIENCE_MASTER_PATH="../Experience Master.docx" \
        .venv/bin/python -m scripts.evaluate_experience_master

What it does, in order:

1. Reads the master file (``.docx`` through python-docx, ``.txt``/``.md`` as
   plain text) and picks at most three live postings: one close fit, one
   partial fit and one stretch role.
2. Runs the real application in-process (no server, no browser) with the real
   provider against a disposable ``resume_tailor_test_master_<random>``
   database: create session -> ingest the master text as one source -> resolve
   conflicts -> confirm -> per posting: analyse the job, generate, open every
   cited evidence record -> delete the session. The database is dropped at the
   end, whatever happened.
3. Computes checks on what came back (``analyse``), prints everything to the
   terminal for a human reviewer, and writes a report file.

Budget: one extraction, one confirmation, at most three job analyses and three
generations. Nothing is retried. A step that fails is reported exactly as the
application answered it; the fake provider is never used in its place.

Privacy: the master file is real personal data. The terminal output shows it
and the generated documents. The report file holds only aggregate numbers,
pass/fail flags, the public postings' titles, companies, URLs and requirement
texts. ``privacy_violations`` checks the report against the master text before
it is written; a report that fails the check is not written.

The API key is loaded by the application's settings layer from the environment
or the project-root ``.env``. This script never reads or prints it.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import time
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from itertools import combinations
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI
from pydantic import BaseModel
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from app.config import REPO_ROOT, TEST_DATABASE_PREFIX, Settings
from app.logging_config import HANDLER_NAME
from app.main import create_app
from app.prompts import PROMPT_VERSION
from app.providers.base import AIProvider, ProviderError, Usage
from app.providers.fake.provider import FakeProvider
from app.providers.openai.provider import OpenAIProvider

BACKEND_DIR = Path(__file__).resolve().parents[1]
FIXTURES_DIR = BACKEND_DIR / "fixtures"
LIVE_JOBS_DIR = FIXTURES_DIR / "jobs" / "live"
SYNTHETIC_JOBS_DIR = FIXTURES_DIR / "jobs" / "synthetic"
OUT_DIR = BACKEND_DIR / "scripts" / "out"

MASTER_PATH_VARIABLE = "EXPERIENCE_MASTER_PATH"
DEFAULT_MASTER_PATH = REPO_ROOT / "Experience Master.docx"
REPORT_PATH_VARIABLE = "MASTER_EVAL_REPORT_PATH"
DEFAULT_REPORT_PATH = OUT_DIR / "experience_master_report.md"
# Optional: comma-separated slugs of the postings to use instead of the default choice.
JOBS_VARIABLE = "MASTER_EVAL_JOBS"
# Optional: a folder OUTSIDE the repository that receives the raw run (personal data).
TRANSCRIPT_VARIABLE = "MASTER_EVAL_TRANSCRIPT_DIR"
# Optional: path of a saved run.json; checks and report are recomputed, nothing is called.
REPLAY_VARIABLE = "MASTER_EVAL_REPLAY"
# Optional: "1" checks this script itself with the fake provider and fictional fixtures.
SELFTEST_VARIABLE = "MASTER_EVAL_SELFTEST"
MONGODB_URI_VARIABLE = "TEST_MONGODB_URI"
DEFAULT_MONGODB_URI = "mongodb://127.0.0.1:27017"

SOURCE_LABEL = "Experience master"
TEST_ORIGIN = "http://127.0.0.1:5173"
FIT_ORDER = ("close", "partial", "stretch")
MAX_JOBS = 3
# With two postings of one fit category, the one named here is used. The Stripe
# posting is a general software role without an AI focus, so the three runs
# ask for three different emphases of the same profile.
PREFERRED_SLUGS = {"partial": "stripe_software_engineer_new_grad"}
SELFTEST_JOBS = {
    "close": "close_fit",
    "partial": "partial_fit",
    "stretch": "stretch_kubernetes",
}

# The hard budget: HTTP requests that start a billable operation ...
REQUEST_BUDGET = {"ingest": 1, "confirm": 1, "job_analysis": MAX_JOBS, "generation": MAX_JOBS}
# ... and the provider calls those requests may make. One generation makes at
# most two generate_documents calls (the draft and the single correction pass)
# and one embedding call; confirming embeds in batches of 50 evidence records.
PROVIDER_CALL_BUDGET = {
    "extract_profile": 1,
    "analyze_job": MAX_JOBS,
    "generate_documents": 2 * MAX_JOBS,
    "embed": 8,
    "verify_claims": MAX_JOBS,
    "regenerate_item": 0,
}

EASTERN = ZoneInfo("America/New_York")
SECTIONS_WITH_ENTRIES = ("experience", "projects", "education", "certifications")
MODEL_WRITTEN_SECTIONS = ("summary", "experience", "projects")
GAP_STATUSES = ("missing", "uncertain")
QUALIFICATION_CATEGORIES = ("skill", "experience", "education", "certification")
NEAR_DUPLICATE_JACCARD = 0.7


class EvaluationError(Exception):
    """The evaluation cannot run or continue; the message is shown to the user."""


class BudgetExceeded(RuntimeError):
    """A call beyond the fixed budget was about to be made."""


def out(*lines: str) -> None:
    """Terminal output (may show personal data; never goes into the report)."""
    for line in lines:
        print(line)


# ---- Reading the master file -------------------------------------------------------


def _read_docx(path: Path) -> str:
    """The document as plain text, one paragraph per line, in document order.

    A list paragraph gets a "- " marker (Word stores the bullet as formatting,
    not as text). A table row becomes one line with " | " between its cells.
    Runs of empty paragraphs become one empty line.
    """
    import docx  # python-docx, a development dependency
    from docx.table import Table

    lines: list[str] = []
    for item in docx.Document(str(path)).iter_inner_content():
        if isinstance(item, Table):
            for row in item.rows:
                cells = [" ".join(cell.text.split()) for cell in row.cells]
                lines.append(" | ".join(cell for cell in cells if cell))
            continue
        text = item.text.strip()
        properties = item._p.pPr
        is_list_item = properties is not None and properties.numPr is not None
        if text and is_list_item and not text.startswith(("- ", "* ", "\u2022")):
            text = f"- {text}"
        if text or (lines and lines[-1]):
            lines.append(text)
    return "\n".join(lines).strip() + "\n"


def read_master(path: Path) -> str:
    if not path.is_file():
        raise EvaluationError(
            f"Experience master file not found: {path}\n"
            f"Set {MASTER_PATH_VARIABLE} to the file's path (.docx, .txt or .md). "
            "Nothing was called and nothing was billed."
        )
    suffix = path.suffix.lower()
    if suffix == ".docx":
        text = _read_docx(path)
    elif suffix in (".txt", ".md"):
        text = path.read_text(encoding="utf-8")
    else:
        raise EvaluationError(
            f"Unsupported experience master format {suffix!r}: use .docx, .txt or .md."
        )
    if not text.strip():
        raise EvaluationError(f"The experience master file is empty: {path}")
    return text


# ---- Postings ----------------------------------------------------------------------


@dataclass(frozen=True)
class Posting:
    """One job posting to test against. Everything here is public."""

    slug: str
    fit: str
    title: str
    company: str
    url: str | None
    date_retrieved: str | None
    description: str
    synthetic: bool

    def public(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "fit": self.fit,
            "title": self.title,
            "company": self.company,
            "url": self.url,
            "date_retrieved": self.date_retrieved,
            "description_chars": len(self.description),
            "synthetic": self.synthetic,
        }


def _live_postings() -> list[Posting]:
    """Every live fixture whose full text was downloaded to ``_local_full/``."""
    postings = []
    for path in sorted(LIVE_JOBS_DIR.glob("*.json")):
        fixture = json.loads(path.read_text(encoding="utf-8"))
        full_text = LIVE_JOBS_DIR / "_local_full" / f"{fixture['slug']}.txt"
        if not full_text.is_file():
            continue
        postings.append(
            Posting(
                slug=fixture["slug"],
                fit=fixture["fit_category"],
                title=fixture["title"],
                company=fixture["company"],
                url=fixture.get("source_url"),
                date_retrieved=fixture.get("date_retrieved"),
                description=full_text.read_text(encoding="utf-8"),
                synthetic=False,
            )
        )
    return postings


def _selftest_postings() -> list[Posting]:
    postings = []
    for fit, slug in SELFTEST_JOBS.items():
        fixture = json.loads((SYNTHETIC_JOBS_DIR / f"{slug}.json").read_text(encoding="utf-8"))
        postings.append(
            Posting(
                slug=slug,
                fit=fit,
                title=fixture["title"],
                company=fixture["company"],
                url=None,
                date_retrieved=None,
                description=fixture["description"],
                synthetic=True,
            )
        )
    return postings


def _selftest_sources() -> list[dict[str, str]]:
    """The fictional candidate's three texts (they disagree about one date, so
    the self-test also exercises conflict resolution)."""
    profiles = FIXTURES_DIR / "profiles"
    return [
        {
            "label": label,
            "source_type": source_type,
            "text": (profiles / name).read_text(encoding="utf-8"),
        }
        for label, source_type, name in (
            ("Resume", "resume", "sample_resume.txt"),
            ("LinkedIn", "linkedin", "sample_linkedin.txt"),
            ("Notes", "notes", "sample_notes.txt"),
        )
    ]


def select_postings(requested: str | None) -> list[Posting]:
    """At most one posting per fit category, in the order close, partial,
    stretch. ``requested`` (comma-separated slugs) overrides the default choice."""
    available = _live_postings()
    if not available:
        raise EvaluationError(
            f"No live posting has its full text under {LIVE_JOBS_DIR / '_local_full'}. "
            "Download it with: python backend/fixtures/jobs/live/fetch_live_jobs.py"
        )
    if requested:
        by_slug = {posting.slug: posting for posting in available}
        slugs = [slug.strip() for slug in requested.split(",") if slug.strip()]
        unknown = [slug for slug in slugs if slug not in by_slug]
        if unknown or not slugs or len(slugs) > MAX_JOBS:
            raise EvaluationError(
                f"{JOBS_VARIABLE} must name one to {MAX_JOBS} of: {', '.join(sorted(by_slug))}"
            )
        return [by_slug[slug] for slug in slugs]
    chosen = []
    for fit in FIT_ORDER:
        candidates = [posting for posting in available if posting.fit == fit]
        preferred = [p for p in candidates if p.slug == PREFERRED_SLUGS.get(fit)]
        if candidates:
            chosen.append((preferred or candidates)[0])
    return chosen[:MAX_JOBS]


# ---- Measuring the provider --------------------------------------------------------


def _plain(value: Any) -> Any:
    """``value`` as JSON-serialisable data (models become dictionaries)."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def text_hash(text: str) -> str:
    return hashlib.sha256(" ".join(text.split()).encode()).hexdigest()[:16]


def feedback_kinds(feedback: list[str]) -> dict[str, int]:
    """The validators' findings handed to the correction pass, counted by
    section and kind. A finding reads ``section: "statement" - reason``; the
    statement and every quoted term are left out."""
    kinds: Counter = Counter()
    for line in feedback:
        section, _, rest = line.partition(":")
        reason = rest.rpartition('" - ')[2]
        kinds[f"{section.strip()}: {reason_template(reason)}"] += 1
    return dict(kinds)


def _call_stats(operation: str, request: Any, result: Any) -> dict[str, Any]:
    """Sizes of one provider call's input and output. Counts and statuses only;
    the one exception, ``raw_rationales``, is used to count how many of the
    model's coverage rationales the server replaced and never leaves this
    process except in the optional transcript."""
    if operation == "embed":
        return {"texts": len(request)}
    if operation == "extract_profile":
        return {
            "records": len(result.records),
            "records_by_category": dict(Counter(record.category for record in result.records)),
            "bullets": sum(len(record.bullets) for record in result.records),
            "ambiguous_records": sum(1 for record in result.records if record.ambiguous),
            "contact_items": len(result.contact),
            "conflicts": len(result.conflicts),
        }
    if operation == "analyze_job":
        return {
            "requirements": len(result.requirements),
            "inferred": sum(1 for requirement in result.requirements if requirement.inferred),
        }
    if operation == "generate_documents":
        context, feedback = request["context"], request["validation_feedback"]
        statements = [item.text for item in [*result.summary, *result.cover_letter]]
        statements += [
            bullet.text
            for entry in [*result.experience, *result.projects]
            for bullet in entry.bullets
        ]
        raw_coverage: dict[str, str] = {}
        raw_rationales: dict[str, str] = {}
        for item in result.coverage:
            # Of several ratings for one requirement the server uses the first.
            raw_coverage.setdefault(item.requirement, item.status)
            raw_rationales.setdefault(item.requirement, item.rationale)
        return {
            "correction_pass": feedback is not None,
            "feedback_items": len(feedback or []),
            "feedback_kinds": feedback_kinds(feedback or []),
            "context_evidence": len(context.evidence),
            "context_records": len(context.records),
            "context_requirements": len(context.requirements),
            "summary_statements": len(result.summary),
            "experience_bullets": sum(len(entry.bullets) for entry in result.experience),
            "project_entries": len(result.projects),
            "project_bullets": sum(len(entry.bullets) for entry in result.projects),
            "skills": len(result.skills),
            "cover_letter_paragraphs": len(result.cover_letter),
            "raw_coverage": raw_coverage,
            "raw_rationales": raw_rationales,
            # Tells afterwards which of two drafts the server kept.
            "statement_hashes": sorted({text_hash(text) for text in statements}),
        }
    return {}


@dataclass
class ProviderCall:
    step: str
    operation: str
    seconds: float
    usage: Usage | None
    error: str | None
    stats: dict[str, Any]
    request: Any = None
    result: Any = None

    def summary(self) -> dict[str, Any]:
        usage = self.usage or Usage()
        return {
            "step": self.step,
            "operation": self.operation,
            "seconds": round(self.seconds, 2),
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "embedding_tokens": usage.embedding_tokens,
            "api_requests": usage.provider_calls,
            "error": self.error,
            "stats": self.stats,
        }


class MeteredProvider:
    """Implements AIProvider by passing every call to the real provider and
    recording how long it took and what it used.

    It never answers on the provider's behalf: an error passes through
    unchanged. It refuses a call beyond PROVIDER_CALL_BUDGET, which the
    application then reports as a failed request.
    """

    def __init__(self, inner: AIProvider) -> None:
        self._inner = inner
        self.mode = inner.mode
        self.generation_model = inner.generation_model
        self.embedding_model = inner.embedding_model
        self.embedding_dimension = inner.embedding_dimension
        self.calls: list[ProviderCall] = []
        # Set by the evaluation before each HTTP request, so calls can be told apart.
        self.step = "-"

    async def _metered[ResultT](
        self, operation: str, request: Any, call: Callable[[], Awaitable[tuple[ResultT, Usage]]]
    ) -> tuple[ResultT, Usage]:
        made = sum(1 for earlier in self.calls if earlier.operation == operation)
        if made >= PROVIDER_CALL_BUDGET[operation]:
            raise BudgetExceeded(f"{operation}: the budget of {made} call(s) is used up")
        started = time.perf_counter()
        try:
            result, usage = await call()
        except Exception as error:
            # ProviderError messages are written by the application; any other
            # exception text could quote user data, so only its type is kept.
            detail = error.message if isinstance(error, ProviderError) else ""
            text = f"{type(error).__name__}: {detail}".rstrip(": ")
            seconds = time.perf_counter() - started
            self.calls.append(ProviderCall(self.step, operation, seconds, None, text, {}, request))
            raise
        seconds = time.perf_counter() - started
        stats = _call_stats(operation, request, result)
        self.calls.append(
            ProviderCall(self.step, operation, seconds, usage, None, stats, request, result)
        )
        return result, usage

    async def embed(self, texts: list[str]) -> tuple[list[list[float]], Usage]:
        return await self._metered("embed", texts, lambda: self._inner.embed(texts))

    async def extract_profile(self, sources: list[Any]) -> tuple[Any, Usage]:
        return await self._metered(
            "extract_profile", sources, lambda: self._inner.extract_profile(sources)
        )

    async def analyze_job(self, job: Any) -> tuple[Any, Usage]:
        return await self._metered("analyze_job", job, lambda: self._inner.analyze_job(job))

    async def generate_documents(self, ctx: Any, feedback: list[str] | None) -> tuple[Any, Usage]:
        request = {"context": ctx, "validation_feedback": feedback}
        return await self._metered(
            "generate_documents", request, lambda: self._inner.generate_documents(ctx, feedback)
        )

    async def regenerate_item(self, ctx: Any, target: Any) -> tuple[Any, Usage]:
        request = {"context": ctx, "target": target}
        return await self._metered(
            "regenerate_item", request, lambda: self._inner.regenerate_item(ctx, target)
        )

    async def verify_claims(self, claims: list[Any]) -> tuple[Any, Usage]:
        return await self._metered(
            "verify_claims", claims, lambda: self._inner.verify_claims(claims)
        )

    async def aclose(self) -> None:
        await self._inner.aclose()


class _LogCapture(logging.Handler):
    """Keeps what the application logs during the run, to check afterwards
    that no profile text reached the log, and counts the SDK's own retries."""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.lines: list[str] = []
        self.sdk_retries = 0

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if record.name.startswith("openai") and message.startswith("Retrying request"):
            self.sdk_retries += 1
        fields = getattr(record, "fields", None) or {}
        self.lines.append(f"{record.name} {message} {json.dumps(fields, default=str)}")


# ---- Running the application -------------------------------------------------------


def build_settings(database: str, *, selftest: bool) -> Settings:
    """Settings of the application under test.

    Whatever decides where data goes and which provider answers is passed
    explicitly; explicit values win over the environment and ``.env``. The API
    key, model names, timeouts and output limits are left to the settings
    layer, exactly as in a deployment.
    """
    explicit: dict[str, Any] = {
        "app_env": "test",
        "ai_provider": "fake" if selftest else "openai",
        "mongodb_uri": os.environ.get(MONGODB_URI_VARIABLE, DEFAULT_MONGODB_URI),
        "mongodb_database": database,
        "cors_origins": [TEST_ORIGIN],
        "trust_proxy_headers": False,
        "ip_hash_salt": "experience-master-evaluation",
        "session_create_limit_per_hour": 10_000,
    }
    if selftest:
        # The self-test must not depend on, or read, the real configuration.
        return Settings(_env_file=None, openai_api_key=None, **explicit)
    return Settings(**explicit)


def _settings_summary(settings: Settings, provider: AIProvider) -> dict[str, Any]:
    """The settings worth reporting. No secret is among them."""
    return {
        "provider_mode": provider.mode,
        "generation_model": provider.generation_model,
        "embedding_model": provider.embedding_model,
        "reasoning_effort": settings.openai_reasoning_effort,
        "provider_timeout_seconds": settings.provider_timeout_seconds,
        "provider_max_retries": settings.provider_max_retries,
        "max_output_tokens_extraction": settings.max_output_tokens_extraction,
        "max_output_tokens_generation": settings.max_output_tokens_generation,
        "semantic_verifier": settings.enable_semantic_verifier,
        "retrieval_per_requirement": settings.retrieval_per_requirement,
        "retrieval_max_context": settings.retrieval_max_context,
        "retrieval_token_budget": settings.retrieval_token_budget,
        "max_profile_chars": settings.max_profile_chars,
        "max_evidence_chunks": settings.max_evidence_chunks,
    }


def iter_claims(generation: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any] | None, dict]]:
    """``(section, entry, claim)`` for every statement of a draft, in document order."""
    resume = generation["resume"]
    for claim in resume["summary"]:
        yield "summary", None, claim
    for section in SECTIONS_WITH_ENTRIES:
        for entry in resume[section]:
            for claim in entry["bullets"]:
                yield section, entry, claim
    for claim in resume["skills"]:
        yield "skills", None, claim
    for claim in generation["cover_letter"]["paragraphs"]:
        yield "cover_letter", None, claim


def referenced_evidence_ids(generation: dict[str, Any]) -> list[str]:
    """Every evidence ID a draft refers to: retrieved context, statements, coverage."""
    evidence_ids = list(generation["retrieved_evidence_ids"])
    for _section, _entry, claim in iter_claims(generation):
        evidence_ids.extend(claim["evidence_ids"])
    for item in generation["coverage"]:
        evidence_ids.extend(item["evidence_ids"])
    return list(dict.fromkeys(evidence_ids))


EDITABLE_RECORD_FIELDS = (
    "record_id",
    "category",
    "title",
    "organization",
    "location",
    "start_date",
    "end_date",
    "summary",
    "skills",
)


def patch_body(profile: dict[str, Any]) -> dict[str, Any]:
    """The PATCH /api/profile body that saves ``profile`` as it is and settles
    every open conflict, which is what a user does on the review screen. No
    value is changed: for a conflict the first source's value stays."""
    records = [
        {name: record[name] for name in EDITABLE_RECORD_FIELDS}
        | {
            "bullets": [
                {"bullet_id": bullet["bullet_id"], "text": bullet["text"]}
                for bullet in record["bullets"]
            ]
        }
        for record in profile["records"]
    ]
    resolutions = [
        {"conflict_id": conflict["conflict_id"], "resolution": "resolved"}
        for conflict in profile["conflicts"]
        if conflict["resolution"] == "unresolved"
    ]
    return {
        "expected_version": profile["version"],
        "contact": profile["contact"],
        "records": records,
        "conflict_resolutions": resolutions,
    }


@dataclass
class Evaluation:
    """Drives the application through its HTTP API and fills in ``run``."""

    http: httpx.AsyncClient
    meter: MeteredProvider
    run: dict[str, Any]
    headers: dict[str, str] = field(default_factory=dict)
    spent: Counter = field(default_factory=Counter)

    def _spend(self, operation: str) -> None:
        if self.spent[operation] >= REQUEST_BUDGET[operation]:
            raise BudgetExceeded(f"{operation}: the budget of {self.spent[operation]} is used up")
        self.spent[operation] += 1

    async def _request(
        self,
        step: str,
        method: str,
        path: str,
        *,
        expect: int,
        json_body: Any = None,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any] | None:
        """One API request. Returns the JSON body, or None after recording the
        failure exactly as the application answered it."""
        self.meter.step = step
        started = time.perf_counter()
        response = await self.http.request(
            method, path, json=json_body, headers=self.headers | (extra_headers or {})
        )
        seconds = time.perf_counter() - started
        record = {
            "step": step,
            "request": f"{method} {path}",
            "status": response.status_code,
            "seconds": round(seconds, 2),
            "ok": response.status_code == expect,
        }
        self.run["steps"].append(record)
        out(f"  {step}: {method} {path} -> {response.status_code} in {seconds:.1f}s")
        if response.status_code == expect:
            return response.json()
        try:
            error = response.json()["error"]
            detail = f"{error['code']}: {error['message']} (request_id {error['request_id']})"
            if error.get("field_errors"):
                detail += f" field_errors={json.dumps(error['field_errors'])}"
            if error.get("details"):
                detail += f" details={json.dumps(error['details'])}"
        except (ValueError, KeyError, TypeError):
            detail = "the response was not the API's error envelope"
        failure = f"{step}: {method} {path} answered HTTP {response.status_code} - {detail}"
        record["failure"] = failure
        self.run["failures"].append(failure)
        out(f"  FAILED {failure}")
        return None

    async def collect(self, sources: list[dict[str, str]], postings: list[Posting]) -> None:
        ready = await self._request("readiness", "GET", "/readyz", expect=200)
        if ready is None:
            return
        session = await self._request("create session", "POST", "/api/sessions", expect=201)
        if session is None:
            return
        self.headers = {"Authorization": f"Bearer {session['token']}"}
        self.run["session"] = {
            "provider_mode": session["provider_mode"],
            "limits": session["limits"],
        }
        try:
            if await self._profile(sources):
                for posting in postings:
                    await self._job(posting)
        finally:
            await self._clear()

    async def _profile(self, sources: list[dict[str, str]]) -> bool:
        """Ingest, review, confirm. True when the profile is ready for generation."""
        self._spend("ingest")
        draft = await self._request(
            "ingest", "POST", "/api/profiles/ingest", expect=200, json_body={"sources": sources}
        )
        self.run["draft_profile"] = draft
        if draft is None:
            return False
        reviewed = draft
        if draft["review_summary"]["unresolved_conflict_count"]:
            reviewed = await self._request(
                "resolve conflicts",
                "PATCH",
                "/api/profile",
                expect=200,
                json_body=patch_body(draft),
            )
            self.run["reviewed_profile"] = reviewed
            if reviewed is None:
                return False
        self._spend("confirm")
        confirmed = await self._request(
            "confirm",
            "POST",
            "/api/profile/confirm",
            expect=200,
            json_body={"expected_version": reviewed["version"]},
        )
        self.run["confirmed_profile"] = confirmed
        return confirmed is not None

    async def _job(self, posting: Posting) -> None:
        entry: dict[str, Any] = {
            "posting": posting.public(),
            "description": posting.description,
            "job": None,
            "generation": None,
            "evidence": {},
        }
        self.run["jobs"].append(entry)
        self._spend("job_analysis")
        job_body = {
            "description": posting.description,
            "title": posting.title,
            "company": posting.company,
        }
        entry["job"] = await self._request(
            f"analyse job [{posting.slug}]", "POST", "/api/jobs", expect=201, json_body=job_body
        )
        if entry["job"] is None:
            return
        self._spend("generation")
        entry["generation"] = await self._request(
            f"generate [{posting.slug}]",
            "POST",
            "/api/generations",
            expect=201,
            json_body={"job_id": entry["job"]["job_id"]},
            extra_headers={"Idempotency-Key": f"master-eval-{uuid.uuid4().hex}"},
        )
        if entry["generation"] is None:
            return
        if entry["generation"]["status"] != "completed":
            self.run["failures"].append(
                f"generate [{posting.slug}]: the draft's status is "
                f"{entry['generation']['status']!r}, not 'completed'"
            )
            return
        await self._open_evidence(posting.slug, entry)

    async def _open_evidence(self, slug: str, entry: dict[str, Any]) -> None:
        """GET every evidence record the draft refers to (no provider call)."""
        self.meter.step = f"open evidence [{slug}]"
        started = time.perf_counter()
        for evidence_id in referenced_evidence_ids(entry["generation"]):
            response = await self.http.get(f"/api/evidence/{evidence_id}", headers=self.headers)
            body = response.json() if response.status_code == 200 else None
            entry["evidence"][evidence_id] = {"status": response.status_code, "body": body}
        seconds = time.perf_counter() - started
        opened = sum(1 for item in entry["evidence"].values() if item["status"] == 200)
        self.run["steps"].append(
            {
                "step": f"open evidence [{slug}]",
                "request": f"GET /api/evidence/{{id}} x {len(entry['evidence'])}",
                "status": 200 if opened == len(entry["evidence"]) else 404,
                "seconds": round(seconds, 2),
                "ok": opened == len(entry["evidence"]),
            }
        )
        out(f"  open evidence [{slug}]: {opened} of {len(entry['evidence'])} opened")

    async def _clear(self) -> None:
        """ "Clear my data", then prove the token no longer works."""
        deleted = await self._request("clear data", "DELETE", "/api/session", expect=200)
        after = await self.http.get("/api/profile", headers=self.headers)
        self.run["cleanup"] = {
            "deleted_counts": deleted["deleted_counts"] if deleted else None,
            "token_rejected_afterwards": after.status_code == 401,
        }


def _code_version() -> dict[str, Any]:
    """Which application code was evaluated: commit, number of files that
    differ from it, and the prompt version. Read-only git commands."""

    def git(*arguments: str) -> str | None:
        try:
            done = subprocess.run(  # noqa: S603 - fixed arguments, no shell
                ["git", "-C", str(REPO_ROOT), *arguments],
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout

    commit = git("rev-parse", "--short", "HEAD")
    changed = git("status", "--porcelain", "--", "backend/app")
    return {
        "commit": commit.strip() if commit else None,
        "uncommitted_files_in_backend_app": len(changed.splitlines())
        if changed is not None
        else None,
        "prompt_version": PROMPT_VERSION,
    }


def _remaining_documents(uri: str, database: str) -> dict[str, int]:
    """Documents left in the collections that hold personal data."""
    names = ("sources", "profiles", "evidence", "jobs", "generations")
    with MongoClient(uri, serverSelectionTimeoutMS=3000) as client:
        return {name: client[database][name].count_documents({}) for name in names}


def _drop_database(uri: str, database: str) -> bool:
    if not database.startswith(TEST_DATABASE_PREFIX):
        raise RuntimeError(f"refusing to drop non-test database {database!r}")
    with MongoClient(uri, serverSelectionTimeoutMS=3000) as client:
        client.drop_database(database)
        return database not in client.list_database_names()


def _capture_application_logs() -> _LogCapture:
    """Send the application's JSON log to stderr (stdout is the reviewer's
    report), keep a copy for the privacy check and make SDK retries visible."""
    capture = _LogCapture()
    root = logging.getLogger()
    for handler in root.handlers:
        if handler.get_name() == HANDLER_NAME and isinstance(handler, logging.StreamHandler):
            handler.setStream(sys.stderr)
    root.addHandler(capture)
    # The SDK announces each automatic retry at INFO level on this logger.
    logging.getLogger("openai._base_client").setLevel(logging.INFO)
    return capture


async def _drive(
    application: FastAPI,
    meter: MeteredProvider,
    run: dict[str, Any],
    postings: list[Posting],
) -> None:
    """Start the application in-process and run the flow against it."""
    async with application.router.lifespan_context(application):
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", timeout=None
        ) as http:
            evaluation = Evaluation(http=http, meter=meter, run=run)
            try:
                await evaluation.collect(run["sources"], postings)
            except BudgetExceeded as error:
                run["failures"].append(f"stopped: {error}")
            except Exception as error:  # noqa: BLE001 - keep what was collected and report it
                run["failures"].append(
                    f"the evaluation script failed while collecting: {type(error).__name__}"
                )
                out(f"  SCRIPT ERROR while collecting: {type(error).__name__}: {error}")
            run["requests_spent"] = dict(evaluation.spent)


def run_evaluation(
    sources: list[dict[str, str]], postings: list[Posting], *, selftest: bool
) -> dict[str, Any]:
    """Run the whole flow once and return the run record (plain data)."""
    database = f"{TEST_DATABASE_PREFIX}_master_{uuid.uuid4().hex[:12]}"
    settings = build_settings(database, selftest=selftest)
    if not selftest and settings.openai_api_key is None:
        raise EvaluationError(
            "OPENAI_API_KEY is not set in the environment or the project-root .env. "
            "Nothing was called and nothing was billed."
        )
    application: FastAPI = create_app(settings)
    capture = _capture_application_logs()
    provider = application.state.provider
    expected_type = FakeProvider if selftest else OpenAIProvider
    if not isinstance(provider, expected_type):
        raise EvaluationError(f"Unexpected provider {type(provider).__name__}; nothing was called.")
    meter = MeteredProvider(provider)
    application.state.provider = meter

    uri = settings.mongodb_uri.get_secret_value()
    run: dict[str, Any] = {
        "mode": "selftest" if selftest else "real",
        "started_at": datetime.now(EASTERN).isoformat(timespec="seconds"),
        "settings": _settings_summary(settings, provider),
        "code": _code_version(),
        "sources": sources,
        "steps": [],
        "failures": [],
        "session": None,
        "draft_profile": None,
        "reviewed_profile": None,
        "confirmed_profile": None,
        "jobs": [],
        "cleanup": None,
    }
    out(f"Database {database} (dropped at the end); provider {provider.mode}")
    started = time.perf_counter()
    try:
        try:
            asyncio.run(_drive(application, meter, run, postings))
        except Exception as error:  # noqa: BLE001 - what was collected is still reported
            run["failures"].append(f"the application could not be driven: {type(error).__name__}")
            out(f"  SCRIPT ERROR: {type(error).__name__}: {error}")
        try:
            run["remaining_documents"] = _remaining_documents(uri, database)
        except PyMongoError as error:
            run["failures"].append(f"could not count leftover documents: {type(error).__name__}")
    finally:
        run["database_dropped"] = _drop_database(uri, database)
        logging.getLogger().removeHandler(capture)
    run["wall_seconds"] = round(time.perf_counter() - started, 1)
    run["provider_calls"] = [call.summary() for call in meter.calls]
    run["sdk_retries"] = capture.sdk_retries
    run["log_lines"] = capture.lines
    run["raw_calls"] = [
        {**call.summary(), "request": _plain(call.request), "result": _plain(call.result)}
        for call in meter.calls
    ]
    return run


# ---- Text helpers for the checks ---------------------------------------------------

_NUMBER = re.compile(r"\d+(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9+#'-]*")
_LIST_MARKER = re.compile(r"^\s*[-*\u2022\u2023\u25e6\u00b7\u2013\u2014]\s+")
_FIRST_PERSON = re.compile(r"\b(?:I|[Mm]y|[Mm]e|[Ww]e|[Oo]ur)\b")
_IRREGULAR_VERBS = frozenset(
    "built led wrote ran won cut made drove grew set took taught gave sped rebuilt "
    "oversaw kept held brought began chose spoke sent spent sold found".split()
)
_MONTHS = {
    name: number
    for number, names in enumerate(
        (
            "jan january",
            "feb february",
            "mar march",
            "apr april",
            "may",
            "jun june",
            "jul july",
            "aug august",
            "sep sept september",
            "oct october",
            "nov november",
            "dec december",
        ),
        start=1,
    )
    for name in names.split()
}
_MONTH_YEAR = re.compile(r"([A-Za-z]{3,9})\.?\s+(\d{4})")
_YEARS_ASKED = re.compile(r"(\d+)\s*\+?\s*(?:or more\s+)?years?\b", re.IGNORECASE)
_DEGREE_ASKED = re.compile(
    r"\b(?:BS|MS|BA|MA|B\.S\.?|M\.S\.?|PhD|Ph\.D\.?|Bachelor(?:'s|\u2019s|s)?"
    r"|Master(?:'s|\u2019s|s)?|degree)\b"
)
_EDGE_PUNCTUATION = "()[]{},;:.!?\"'\u201c\u201d\u2018\u2019"
_BREAKS_A_PHRASE = ",;:.!?)("


def squash(text: str) -> str:
    """Whitespace-normalised text, for "is this passage in that text" checks."""
    return " ".join(text.split())


def words(text: str) -> list[str]:
    return [word.lower() for word in _WORD.findall(text)]


def numbers_in(text: str) -> set[str]:
    """The numbers written in ``text`` in one comparable form: no thousands
    separators, no trailing zeros ("5,000" and "5000" are the same number)."""
    found = set()
    for match in _NUMBER.finditer(text):
        value = match.group().replace(",", "")
        if "." in value:
            value = value.rstrip("0").rstrip(".")
        found.add(value.lstrip("0") or "0")
    return found


def jaccard(first: set[Any], second: set[Any]) -> float | None:
    union = first | second
    return round(len(first & second) / len(union), 2) if union else None


def reason_template(text: str) -> str:
    """A server-written message without the terms and figures it quotes, so
    messages can be counted by kind and printed without profile content."""
    text = re.sub(r'"[^"]*"|\u201c[^\u201d]*\u201d', '"..."', text)
    text = re.sub(r"\d+(?:[.,]\d+)*%?", "N", text)
    return squash(text)


def _term_pattern(term: str) -> re.Pattern[str]:
    """Whole-term match. Short terms and acronyms ("Go", "MS", "k8s") must
    match in the same letter case, or "go" and "ms" would count as mentions."""
    exact_case = len(term) <= 3 or term.isupper()
    return re.compile(
        r"(?<![\w+#])" + re.escape(term) + r"(?![\w+#])", 0 if exact_case else re.IGNORECASE
    )


def mentions(text: str, term: str) -> bool:
    """Whether ``text`` has ``term`` as a whole term, in its singular ("REST
    APIs" is mentioned by "REST API") or, for a name of several words, as its
    initials ("Machine Learning" is mentioned by "ML")."""
    forms = [term, term[:-1]] if len(term) > 3 and term.endswith("s") else [term]
    parts = re.split(r"[\s-]+", term)
    if len(parts) > 1 and all(part[:1].isalpha() for part in parts):
        forms.append("".join(part[0].upper() for part in parts))
    return any(_term_pattern(form).search(text) for form in forms)


# ---- Checks: extraction ------------------------------------------------------------

# Review reasons the application writes itself (app/services/ingestion.py). Any
# other reason is a note written by the model about the profile, so its text is
# counted but never printed in the report.
_SERVER_REVIEW_REASONS = (
    re.compile(r"^source span not found$"),
    re.compile(r"^The extraction marked this item as ambiguous\.$"),
    re.compile(r"^No dates were found for this item\.$"),
    re.compile(r"^Only the first N statements were kept\.$"),
    re.compile(r"^A line that reads like an instruction to an AI system"),
    re.compile(r"^The [a-z ]+ was not found in the source text\.$"),
    re.compile(r"^The [a-z ]+ was longer than N characters and was shortened\.$"),
    re.compile(r"^A skill longer than N characters was left out\.$"),
    re.compile(r'^The skill "\.\.\." was left out: it is not in the source text\.$'),
)
MODEL_NOTE = "ambiguity note written by the model (text withheld)"


def review_reason_kind(reason: str) -> str:
    template = reason_template(reason)
    known = any(pattern.search(template) for pattern in _SERVER_REVIEW_REASONS)
    return template if known else MODEL_NOTE


def source_texts(run: dict[str, Any], profile: dict[str, Any]) -> dict[str, str]:
    """source_id -> the text that was submitted under that source's label."""
    submitted = {source["label"]: source["text"] for source in run["sources"]}
    return {source["source_id"]: submitted[source["label"]] for source in profile["sources"]}


def master_text(run: dict[str, Any]) -> str:
    """Everything that was submitted as profile text."""
    return "\n".join(source["text"] for source in run["sources"])


def record_labels(profile: dict[str, Any]) -> dict[str, str]:
    """record_id -> an anonymous label such as "employment 2" (category and
    position in the profile), used wherever a record is named in the report."""
    counters: Counter = Counter()
    labels = {}
    for record in profile["records"]:
        counters[record["category"]] += 1
        labels[record["record_id"]] = f"{record['category']} {counters[record['category']]}"
    return labels


def _span_is_exact(ref: dict[str, Any] | None, sources: dict[str, str]) -> bool:
    """``excerpt == original_source_text[start:end]`` and it is not blank."""
    if ref is None or ref["source_id"] not in sources:
        return False
    original = sources[ref["source_id"]]
    return bool(ref["excerpt"].strip()) and ref["excerpt"] == original[ref["start"] : ref["end"]]


LINE_COVERAGE_KEYS = (
    "lines",
    "covered_by_span",
    "covered_by_text",
    "covered_by_header_fact",
    "covered_as_contact",
    "covered_as_skills",
    "section_labels",
    "uncovered",
)
_HEADING_LINE = re.compile(r"[^\d,.;|]+")
# A comma that separates list items, not one inside "(a, b)".
_TOP_LEVEL_COMMA = re.compile(r",(?![^()]*\))")


def _source_line_coverage(profile: dict[str, Any], sources: dict[str, str]) -> dict[str, Any]:
    """How much of the submitted text ended up in the profile, line by line.

    A non-empty line counts as covered when an extracted source span overlaps
    it, when it is contained in (or contains most of) an extracted summary or
    statement, when it names a record's title or organisation or both of its
    dates (a header repeated by a second source), when it holds a contact
    detail, or when it is a list of which at least half the items became
    skills. A line of at most four words without figures or punctuation, or a
    short line ending in a colon, is a section label and is counted
    separately. Uncovered lines are returned for the terminal only.
    """
    spans: dict[str, list[tuple[int, int]]] = {source_id: [] for source_id in sources}
    statements = []
    skills: set[str] = set()
    # Each entry: the strings that must all be on a line for it to restate a header.
    header_facts: list[list[str]] = []
    for record in profile["records"]:
        refs = [record["source_ref"], *(bullet["source_ref"] for bullet in record["bullets"])]
        for ref in refs:
            if ref and ref["source_id"] in spans:
                spans[ref["source_id"]].append((ref["start"], ref["end"]))
        statements += [squash(bullet["text"]).lower() for bullet in record["bullets"]]
        if record["summary"]:
            statements.append(squash(record["summary"]).lower())
        statements.append(squash(record["title"]).lower())
        skills.update(skill.lower() for skill in record["skills"])
        if record["category"] != "skill":
            names = [record["title"], record["organization"] or ""]
            header_facts += [[squash(name).lower()] for name in names if len(name) >= 4]
            if record["start_date"] and record["end_date"]:
                header_facts.append([record["start_date"].lower(), record["end_date"].lower()])
    contact = profile["contact"]
    contact_values = [
        squash(value).lower()
        for value in [*contact["links"], *(contact[name] for name in contact if name != "links")]
        if value
    ]

    counts: Counter = Counter()
    uncovered = []
    for source_id, text in sources.items():
        position = 0
        for raw_line in text.splitlines(keepends=True):
            start, end = position, position + len(raw_line)
            position = end
            line = squash(_LIST_MARKER.sub("", raw_line))
            if not line:
                continue
            counts["lines"] += 1
            folded = line.lower()
            # The text after a leading "Label:", which extraction leaves out.
            unlabelled = folded.partition(":")[2].strip() or folded
            items = [item.strip(" .") for item in _TOP_LEVEL_COMMA.split(unlabelled)]
            items = [item for item in items if item]
            listed = sum(1 for item in items if item in skills)
            if any(first < end and start < last for first, last in spans[source_id]):
                counts["covered_by_span"] += 1
            elif any(
                folded in statement
                or unlabelled in statement
                or (2 * len(statement) >= len(folded) and statement in folded)
                for statement in statements
            ):
                counts["covered_by_text"] += 1
            elif any(all(fact in folded for fact in facts) for facts in header_facts):
                counts["covered_by_header_fact"] += 1
            elif any(value in folded for value in contact_values):
                counts["covered_as_contact"] += 1
            elif len(items) >= 2 and 2 * listed >= len(items):
                counts["covered_as_skills"] += 1
            elif (line.endswith(":") and len(line) <= 60) or (
                len(line.split()) <= 4 and _HEADING_LINE.fullmatch(line)
            ):
                counts["section_labels"] += 1
            else:
                counts["uncovered"] += 1
                uncovered.append(line)
    return {**{key: counts[key] for key in LINE_COVERAGE_KEYS}, "uncovered_lines": uncovered}


def extraction_checks(run: dict[str, Any]) -> dict[str, Any] | None:
    profile = run["draft_profile"]
    if profile is None:
        return None
    sources = source_texts(run, profile)
    squashed_source = squash(master_text(run))
    records = profile["records"]
    bullets = [bullet for record in records for bullet in record["bullets"]]
    items = [*records, *bullets]
    exact = sum(1 for item in items if _span_is_exact(item["source_ref"], sources))

    header_fields = [
        value
        for record in records
        if record["category"] != "skill"
        for value in (
            record["title"],
            record["organization"],
            record["location"],
            record["start_date"],
            record["end_date"],
        )
        if value
    ]
    summaries = [record["summary"] for record in records if record["summary"]]
    reasons = [
        reason for item in items if item["needs_review"] for reason in item["review_reasons"]
    ]
    extracted_words = sum(len(words(bullet["text"])) for bullet in bullets) + sum(
        len(words(summary)) for summary in summaries
    )
    raw = next(
        (
            call["stats"]
            for call in run["provider_calls"]
            if call["operation"] == "extract_profile" and not call["error"]
        ),
        {},
    )
    return {
        "source_chars": sum(len(text) for text in sources.values()),
        "source_words": len(words(master_text(run))),
        "records": len(records),
        "records_by_category": dict(Counter(record["category"] for record in records)),
        "model_records": raw.get("records"),
        "model_records_by_category": raw.get("records_by_category"),
        "bullets": len(bullets),
        "skills": sum(len(record["skills"]) for record in records),
        "records_with_span": sum(1 for record in records if record["source_ref"]),
        "bullets_with_span": sum(1 for bullet in bullets if bullet["source_ref"]),
        "spans_exact": exact,
        "span_items": len(items),
        "span_exact_share": round(exact / len(items), 3) if items else None,
        "records_needing_review": sum(1 for record in records if record["needs_review"]),
        "bullets_needing_review": sum(1 for bullet in bullets if bullet["needs_review"]),
        "review_reasons": reasons,
        "review_reason_kinds": dict(Counter(review_reason_kind(reason) for reason in reasons)),
        "conflicts": len(profile["conflicts"]),
        "unresolved_conflicts": profile["review_summary"]["unresolved_conflict_count"],
        "contact_fields_found": sorted(name for name, value in profile["contact"].items() if value),
        "header_fields": len(header_fields),
        "header_fields_verbatim_in_source": sum(
            1 for value in header_fields if squash(value) in squashed_source
        ),
        "summaries": len(summaries),
        "summaries_verbatim_in_source": sum(
            1 for summary in summaries if squash(summary) in squashed_source
        ),
        "dated_records_without_dates": sum(
            1
            for record in records
            if record["category"] in ("employment", "education")
            and not (record["start_date"] or record["end_date"])
        ),
        "statement_words": extracted_words,
        "line_coverage": _source_line_coverage(profile, sources),
    }


# ---- Checks: one job ---------------------------------------------------------------


def _header_mismatches(generation: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    """Fact preservation: every header shown in the resume is the confirmed
    record's own title, organisation, location and date strings."""
    records = {record["record_id"]: record for record in profile["records"]}
    resume = generation["resume"]
    problems = []
    entries = 0
    shown_ids = []
    for section in SECTIONS_WITH_ENTRIES:
        for entry in resume[section]:
            entries += 1
            shown_ids.append(entry["record_id"])
            record = records.get(entry["record_id"])
            if record is None:
                problems.append(f"{section}: an entry refers to no confirmed record")
                continue
            for name, shown, confirmed in (
                ("heading", entry["heading"], record["title"]),
                ("subheading", entry["subheading"], record["organization"]),
                ("location", entry["location"], record["location"]),
            ):
                if shown != confirmed:
                    problems.append(f"{section}: {name} differs from the confirmed record")
            remainder = entry["date_range"] or ""
            for date in (record["start_date"], record["end_date"]):
                if date and date not in remainder:
                    problems.append(f"{section}: a confirmed date string is not shown")
                remainder = remainder.replace(date or "\0", "", 1)
            if remainder.strip(" -\u2013\u2014"):
                problems.append(
                    f"{section}: the date range shows text that is not a confirmed date"
                )
    employment = [r["record_id"] for r in profile["records"] if r["category"] == "employment"]
    shown_roles = [entry["record_id"] for entry in resume["experience"]]
    if shown_roles != employment:
        problems.append("experience: the roles shown are not the confirmed roles in profile order")
    if len(shown_ids) != len(set(shown_ids)):
        problems.append("a confirmed record is shown more than once")
    if resume["contact"] != profile["contact"]:
        problems.append("contact: differs from the confirmed contact details")
    return {"entries": entries, "problems": problems, "passed": not problems}


def _evidence_text(body: dict[str, Any]) -> str:
    parent = body.get("parent") or {}
    parts = [body["excerpt"], body["text"], ", ".join(body["tags"])]
    parts += [parent.get("title") or "", parent.get("organization") or ""]
    return "\n".join(part for part in parts if part)


def _number_check(entry: dict[str, Any]) -> dict[str, Any]:
    """Every number in every statement occurs in the evidence that statement cites."""
    generation, evidence = entry["generation"], entry["evidence"]
    posting_numbers = numbers_in(entry["description"])
    with_numbers = checked = 0
    problems = []
    for section, _entry, claim in iter_claims(generation):
        claimed = numbers_in(claim["text"])
        if not claimed:
            continue
        with_numbers += 1
        checked += len(claimed)
        cited = "\n".join(
            _evidence_text(evidence[key]["body"])
            for key in claim["evidence_ids"]
            if evidence.get(key, {}).get("body")
        )
        absent = sorted(claimed - numbers_in(cited))
        if absent:
            problems.append(
                {
                    "section": section,
                    "status": claim["validation_status"],
                    "numbers": absent,
                    "cites_evidence": bool(claim["evidence_ids"]),
                    "all_in_posting": all(number in posting_numbers for number in absent),
                    "text": claim["text"],
                }
            )
    return {
        "claims_with_numbers": with_numbers,
        "numbers_checked": checked,
        "numbers_not_in_cited_evidence": sum(len(problem["numbers"]) for problem in problems),
        "problems": problems,
        "passed": not problems,
    }


def _evidence_check(entry: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    """Every referenced evidence ID opens through the API as that record,
    without vectors, and a located excerpt is exactly that slice of the source."""
    sources = source_texts(run, run["confirmed_profile"])
    referenced = referenced_evidence_ids(entry["generation"])
    resolved = wrong_slice = located = 0
    for evidence_id in referenced:
        item = entry["evidence"].get(evidence_id)
        body = item["body"] if item else None
        if not body or body["evidence_id"] != evidence_id or "embedding" in body:
            continue
        resolved += 1
        source = body["source"]
        if source["start"] is not None and source["source_id"] in sources:
            located += 1
            original = sources[source["source_id"]]
            if body["excerpt"] != original[source["start"] : source["end"]]:
                wrong_slice += 1
    return {
        "referenced": len(referenced),
        "resolved": resolved,
        "with_source_offsets": located,
        "excerpt_not_the_source_slice": wrong_slice,
        "passed": resolved == len(referenced) and wrong_slice == 0 and bool(referenced),
    }


def _coverage_check(generation: dict[str, Any]) -> dict[str, Any]:
    summary = generation["coverage_summary"]
    counts = Counter(item["status"] for item in generation["coverage"])
    assessed = counts["supported"] + counts["partial"] + counts["missing"]
    percent = (
        round(100 * (counts["supported"] + 0.5 * counts["partial"]) / assessed, 1)
        if assessed
        else None
    )
    recomputed = {
        "supported": counts["supported"],
        "partial": counts["partial"],
        "missing": counts["missing"],
        "uncertain": counts["uncertain"],
        "assessed": assessed,
        "percent": percent,
    }
    return {
        **{name: summary[name] for name in recomputed},
        "summary_matches_items": recomputed == {name: summary[name] for name in recomputed},
        "requirements": [
            {
                "status": item["status"],
                "importance": item["importance"],
                "text": item["requirement_text"],
                "cited": len(item["evidence_ids"]),
                "rationale": item["rationale"],
            }
            for item in generation["coverage"]
        ],
    }


def _is_sentence_start(text: str, start: int) -> bool:
    """Whether position ``start`` begins a line, a list item or a sentence."""
    line_start = text.rfind("\n", 0, start) + 1
    before = _LIST_MARKER.sub("", text[line_start:start] + "x")[:-1].rstrip()
    return not before or before[-1] in ".!?:"


def _name_form(term: str, text: str) -> tuple[bool, str | None]:
    """``(occurs, form)``: whether ``text`` has ``term`` at all, and the form in
    which it writes the term as a name: with a digit, with a capital after its
    first letter, or with a capital first letter somewhere other than the
    start of a sentence. ``form`` is None when it is never written that way."""
    pattern = re.compile(r"(?<![\w+#])" + re.escape(term) + r"(?![\w+#])", re.IGNORECASE)
    matches = list(pattern.finditer(text))
    for match in matches:
        written = match.group()
        if any(char.isdigit() for char in written) or any(c.isupper() for c in written[1:]):
            return True, written
        if written[0].isupper() and not _is_sentence_start(text, match.start()):
            return True, written
    return bool(matches), None


def named_terms(requirement: dict[str, Any], posting_text: str) -> list[str]:
    """The technologies, products and qualifications a requirement names, as
    the posting spells them.

    Candidates are the requirement's keywords and every run of words in its
    text that carry a capital or a digit (the first word of a sentence on its
    own, since its capital may only mark the sentence). A candidate counts when
    the posting writes it as a name (see _name_form), so "Kubernetes", "k8s",
    "Ray Data" and "AWS" count while "Hands-on" and "Experience" at the start
    of a line do not.
    """
    candidates = [squash(keyword) for keyword in requirement.get("keywords", [])]
    phrase: list[str] = []
    sentence_start = True
    for raw in [*requirement["text"].split(), "."]:
        token = raw.strip(_EDGE_PUNCTUATION)
        name_like = any(c.isalpha() for c in token) and any(
            c.isupper() or c.isdigit() for c in token
        )
        only_initial_capital = name_like and not any(c.isupper() or c.isdigit() for c in token[1:])
        if phrase and (not name_like or raw[0] in _BREAKS_A_PHRASE):
            candidates.append(" ".join(phrase))
            phrase = []
        if name_like and sentence_start and only_initial_capital:
            candidates.append(token)
        elif name_like:
            phrase.append(token)
            if raw[-1] in _BREAKS_A_PHRASE:
                candidates.append(" ".join(phrase))
                phrase = []
        sentence_start = raw[-1] in ".!?:"
    named: dict[str, str] = {}
    for candidate in candidates:
        if not candidate:
            continue
        occurs, form = _name_form(candidate, posting_text)
        if not occurs:
            _occurs, form = _name_form(candidate, requirement["text"])
        if form:
            named.setdefault(form.lower(), form)
    return list(named.values())


def employment_months(profile: dict[str, Any], today: datetime) -> int | None:
    """Calendar months covered by the confirmed employment dates (overlaps
    counted once), or None when a date cannot be read as "Month YYYY"."""
    covered: set[tuple[int, int]] = set()
    for record in profile["records"]:
        if record["category"] != "employment":
            continue
        ends = []
        for value in (record["start_date"], record["end_date"]):
            match = _MONTH_YEAR.search(value or "")
            month = _MONTHS.get(match.group(1).lower()) if match else None
            if match and month:
                ends.append((int(match.group(2)), month))
            elif value and value.strip().lower() in ("present", "current", "now", "ongoing"):
                ends.append((today.year, today.month))
            else:
                return None
        (year, month), last = ends[0], ends[1]
        while (year, month) <= last:
            covered.add((year, month))
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return len(covered)


def _gap_check(entry: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    """Explicit qualifications the posting asks for that the profile lacks, and
    how the draft rated them. A qualification (a requirement the job analysis
    put in the category skill, experience, education or certification; duties
    and culture statements are not qualifications) counts as lacking when

    - it asks for N+ years and the confirmed employment dates cover fewer, or
    - it asks for a degree and the confirmed profile has no education record, or
    - it names technologies or qualifications none of which occurs anywhere in
      the master text. The employer's own name is not such a term.

    The rule is deliberately narrow: a requirement it does not catch may still
    be a gap, and is left to the reviewer.
    """
    profile, source_text = run["confirmed_profile"], master_text(run)
    months = employment_months(profile, datetime.fromisoformat(run["started_at"]))
    has_education = any(record["category"] == "education" for record in profile["records"])
    requirements = {r["requirement_id"]: r for r in entry["job"]["requirements"]}
    employer = [word.lower() for word in words(entry["posting"]["company"]) if len(word) >= 3]
    lacking = []
    invented_terms: set[str] = set()
    absent_terms: set[str] = set()
    for item in entry["generation"]["coverage"]:
        requirement = requirements.get(item["requirement_id"])
        if requirement is None:
            continue
        named = [
            term
            for term in named_terms(requirement, entry["description"])
            if not any(word.startswith(name) for word in words(term) for name in employer)
        ]
        absent = [term for term in named if not mentions(source_text, term)]
        absent_terms.update(absent)
        if requirement["category"] not in QUALIFICATION_CATEGORIES:
            continue
        rules = []
        years = _YEARS_ASKED.search(requirement["text"])
        if years and months is not None and months < 12 * int(years.group(1)):
            rules.append(f"asks for {years.group(1)}+ years; the employment dates cover fewer")
        if _DEGREE_ASKED.search(requirement["text"]) and not has_education:
            rules.append("asks for a degree; the profile has no education record")
        if named and len(absent) == len(named):
            rules.append("names only terms absent from the master text: " + ", ".join(named))
        if rules:
            lacking.append(
                {
                    "text": item["requirement_text"],
                    "importance": item["importance"],
                    "status": item["status"],
                    "rules": rules,
                }
            )
    # Nothing invented: a term the master text never mentions must not appear
    # in the resume, nor in a cover-letter paragraph that cites evidence.
    for section, _entry, claim in iter_claims(entry["generation"]):
        if section == "cover_letter" and not claim["evidence_ids"]:
            continue
        invented_terms.update(term for term in absent_terms if mentions(claim["text"], term))
    rated_supported = [item for item in lacking if item["status"] == "supported"]
    return {
        "employment_months": months,
        "lacking": lacking,
        "lacking_rated_supported": len(rated_supported),
        "absent_terms_named_by_posting": sorted(absent_terms),
        "absent_terms_claimed": sorted(invented_terms),
        "passed": bool(lacking) and not rated_supported,
        "nothing_invented": not invented_terms,
    }


def _starts_with_verb(text: str) -> bool:
    """Heuristic: the first word is a capitalised past-tense verb."""
    first = (_WORD.findall(text) or [""])[0]
    lowered = first.lower().rpartition("-")[2]
    return first[:1].isupper() and (lowered.endswith("ed") or lowered in _IRREGULAR_VERBS)


def _quality(entry: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    """Numbers that describe the documents' shape. They inform the reviewer's
    judgement; none of them is a quality score."""
    generation, profile = entry["generation"], run["confirmed_profile"]
    resume = generation["resume"]
    bullets = [
        claim
        for section in ("experience", "projects")
        for item in resume[section]
        for claim in item["bullets"]
    ]
    lengths = [len(words(claim["text"])) for claim in bullets]
    texts = [squash(claim["text"]).lower().rstrip(".") for claim in [*resume["summary"], *bullets]]
    token_sets = [{word for word in words(text) if len(word) >= 3} for text in texts]
    near = sum(
        1
        for first, second in combinations(token_sets, 2)
        if first and second and len(first & second) / len(first | second) >= NEAR_DUPLICATE_JACCARD
    )
    citations = Counter(evidence_id for claim in bullets for evidence_id in claim["evidence_ids"])
    header_words = sum(
        len(
            words(
                " ".join(
                    str(item[name] or "")
                    for name in ("heading", "subheading", "location", "date_range")
                )
            )
        )
        for section in SECTIONS_WITH_ENTRIES
        for item in resume[section]
    )
    resume_words = header_words + sum(
        len(words(claim["text"]))
        for section, _entry, claim in iter_claims(generation)
        if section != "cover_letter"
    )
    letter = generation["cover_letter"]["paragraphs"]
    shown = {
        item["record_id"]: len(item["bullets"])
        for section in SECTIONS_WITH_ENTRIES
        for item in resume[section]
    }
    by_category: dict[str, dict[str, int]] = {}
    for record in profile["records"]:
        if record["category"] == "skill":
            continue
        counts = by_category.setdefault(
            record["category"], {"in_profile": 0, "shown": 0, "shown_without_bullets": 0}
        )
        counts["in_profile"] += 1
        if record["record_id"] in shown:
            counts["shown"] += 1
            counts["shown_without_bullets"] += shown[record["record_id"]] == 0
    drafts = [
        call["stats"]
        for call in run["provider_calls"]
        if call["operation"] == "generate_documents"
        and call["step"] == f"generate [{entry['posting']['slug']}]"
        and not call["error"]
    ]
    final = {item["requirement_id"]: item for item in generation["coverage"]}
    order = [requirement["requirement_id"] for requirement in entry["job"]["requirements"]]
    lowered = replaced = 0
    kept_draft = None
    if drafts:
        # After a correction pass the server keeps whichever draft has fewer
        # unsupported statements: the one whose wording the final draft shows.
        written = {
            text_hash(claim["text"])
            for section, _entry, claim in iter_claims(generation)
            if section in (*MODEL_WRITTEN_SECTIONS, "cover_letter")
        }
        overlaps = [len(written & set(stats["statement_hashes"])) for stats in drafts]
        kept_draft = max(range(len(drafts)), key=lambda number: (overlaps[number], number))
        raw_status = drafts[kept_draft]["raw_coverage"]
        raw_rationale = drafts[kept_draft].get("raw_rationales", {})
        rank = {"supported": 3, "partial": 2, "uncertain": 1, "missing": 0}
        for number, requirement_id in enumerate(order, start=1):
            item, alias = final.get(requirement_id), f"R{number}"
            if item is None or alias not in raw_status:
                continue
            lowered += rank[item["status"]] < rank[raw_status[alias]]
            if item["status"] != "missing":
                replaced += squash(raw_rationale.get(alias, ""))[:600] != item["rationale"]
    return {
        "bullets": len(bullets),
        "bullet_words_mean": round(sum(lengths) / len(lengths), 1) if lengths else None,
        "bullet_words_max": max(lengths, default=None),
        "bullets_over_30_words": sum(1 for length in lengths if length > 30),
        "bullets_starting_with_verb": sum(1 for c in bullets if _starts_with_verb(c["text"])),
        "bullets_in_first_person": sum(1 for c in bullets if _FIRST_PERSON.search(c["text"])),
        "bullets_ending_with_full_stop": sum(1 for c in bullets if c["text"].endswith(".")),
        "exact_duplicate_statements": len(texts) - len(set(texts)),
        "near_duplicate_pairs": near,
        "evidence_cited_by_several_bullets": sum(1 for count in citations.values() if count > 1),
        "summary_sentences": len(resume["summary"]),
        "summary_words": sum(len(words(claim["text"])) for claim in resume["summary"]),
        "skills": len(resume["skills"]),
        "resume_words": resume_words,
        "cover_letter_paragraphs": len(letter),
        "cover_letter_words": sum(len(words(claim["text"])) for claim in letter),
        "cover_letter_paragraphs_citing_evidence": sum(1 for c in letter if c["evidence_ids"]),
        "records_by_category": by_category,
        "generation_calls": len(drafts),
        "correction_pass_feedback_items": drafts[-1]["feedback_items"] if drafts else None,
        "correction_pass_feedback_kinds": drafts[-1].get("feedback_kinds", {}) if drafts else {},
        "kept_draft": None if kept_draft is None else kept_draft + 1,
        "model_bullets_in_kept_draft": (
            None
            if kept_draft is None
            else drafts[kept_draft]["experience_bullets"] + drafts[kept_draft]["project_bullets"]
        ),
        "coverage_ratings_lowered_by_server": lowered,
        "coverage_rationales_replaced_by_server": replaced,
    }


def _emphasis(entry: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    """Where this draft puts its weight: bullets and citations per record."""
    generation, labels = entry["generation"], record_labels(run["confirmed_profile"])
    resume = generation["resume"]
    home = {
        evidence_id: (item["body"].get("parent") or {}).get("record_id")
        for evidence_id, item in entry["evidence"].items()
        if item["body"]
    }
    bullets_per_record = {
        labels.get(item["record_id"], "?"): len(item["bullets"])
        for section in ("experience", "projects")
        for item in resume[section]
    }
    cited = {
        evidence_id
        for section, _entry, claim in iter_claims(generation)
        if section in MODEL_WRITTEN_SECTIONS
        for evidence_id in claim["evidence_ids"]
    }
    retrieved_per_record = Counter(
        labels.get(home.get(evidence_id) or "", "no record")
        for evidence_id in generation["retrieved_evidence_ids"]
    )

    def records_of(claims: list[dict[str, Any]]) -> list[str]:
        found = [
            labels.get(home.get(evidence_id) or "", "no record")
            for claim in claims
            for evidence_id in claim["evidence_ids"]
        ]
        return list(dict.fromkeys(found))

    letter_body = [c for c in generation["cover_letter"]["paragraphs"] if c["evidence_ids"]]
    most = max(bullets_per_record.values(), default=0)
    return {
        "cited_evidence_ids": sorted(cited),
        "retrieved_evidence_ids": list(generation["retrieved_evidence_ids"]),
        "skill_names": [claim["text"].lower() for claim in resume["skills"]],
        "bullets_per_record": bullets_per_record,
        "records_with_most_bullets": [
            label for label, count in bullets_per_record.items() if count == most and most
        ],
        "project_section_order": [
            labels.get(item["record_id"], "?") for item in resume["projects"]
        ],
        "summary_cites": records_of(resume["summary"]),
        "first_letter_body_cites": records_of(letter_body[:1]),
        "retrieved_per_record": dict(retrieved_per_record),
    }


def job_checks(entry: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    """All checks of one posting. ``state`` says how far the flow got."""
    job, generation = entry["job"], entry["generation"]
    result: dict[str, Any] = {"posting": entry["posting"], "state": "job analysis failed"}
    if job is None:
        return result
    requirements = job["requirements"]
    result["job"] = {
        "requirements": len(requirements),
        "by_importance": dict(Counter(r["importance"] for r in requirements)),
        "inferred": sum(1 for r in requirements if r["inferred"]),
        "with_located_quote": sum(1 for r in requirements if r["source_span"]),
        "quotes_exact": sum(
            1
            for r in requirements
            if r["source_span"]
            and r["source_span"]["excerpt"]
            == entry["description"][r["source_span"]["start"] : r["source_span"]["end"]]
        ),
    }
    result["state"] = "generation failed"
    if generation is None or generation["status"] != "completed":
        return result
    result["state"] = "completed"
    claims = list(iter_claims(generation))
    result["draft"] = {
        "provider_mode": generation["provider_mode"],
        "model": generation["model"],
        "usage": generation["usage"],
        "retrieved": len(generation["retrieved_evidence_ids"]),
        "claims": len(claims),
        "validation_status": dict(Counter(claim["validation_status"] for _s, _e, claim in claims)),
        "validation_status_by_section": {
            section: dict(Counter(c["validation_status"] for s, _e, c in claims if s == section))
            for section in dict.fromkeys(section for section, _e, _c in claims)
        },
        "validation_summary": generation["validation"],
        "warning_kinds": dict(
            Counter(reason_template(w) for _s, _e, c in claims for w in c["warnings"])
        ),
        "omitted": len(generation["omitted_claims"]),
        "omitted_by_section": dict(Counter(o["section"] for o in generation["omitted_claims"])),
        "omitted_reason_kinds": dict(
            Counter(reason_template(o["reason"]) for o in generation["omitted_claims"])
        ),
        "draft_warnings": [reason_template(warning) for warning in generation["warnings"]],
    }
    result["facts"] = _header_mismatches(generation, run["confirmed_profile"])
    result["numbers"] = _number_check(entry)
    result["evidence"] = _evidence_check(entry, run)
    result["coverage"] = _coverage_check(generation)
    result["gaps"] = _gap_check(entry, run)
    result["quality"] = _quality(entry, run)
    result["emphasis"] = _emphasis(entry, run)
    return result


def analyse(run: dict[str, Any]) -> dict[str, Any]:
    """Every check, computed from the run record alone (so a saved run can be
    analysed again without calling anything)."""
    jobs = [job_checks(entry, run) for entry in run["jobs"]]
    done = [job for job in jobs if job["state"] == "completed"]
    pairs = []
    for first, second in combinations(done, 2):
        a, b = first["emphasis"], second["emphasis"]
        pairs.append(
            {
                "jobs": [first["posting"]["slug"], second["posting"]["slug"]],
                "cited_evidence_jaccard": jaccard(
                    set(a["cited_evidence_ids"]), set(b["cited_evidence_ids"])
                ),
                "retrieved_evidence_jaccard": jaccard(
                    set(a["retrieved_evidence_ids"]), set(b["retrieved_evidence_ids"])
                ),
                "skills_jaccard": jaccard(set(a["skill_names"]), set(b["skill_names"])),
                "same_skill_order": a["skill_names"] == b["skill_names"],
            }
        )
    profile = run["confirmed_profile"]
    usage = Counter()
    for call in run["provider_calls"]:
        for name in ("input_tokens", "output_tokens", "embedding_tokens", "api_requests"):
            usage[name] += call[name]
    return {
        "extraction": extraction_checks(run),
        "index": profile
        and {
            "status": profile["status"],
            "index_state": profile["index_state"],
            "chunks": profile["index_progress"]["total"],
            "embedded": profile["index_progress"]["embedded"],
        },
        "jobs": jobs,
        "emphasis_pairs": pairs,
        "usage_total": dict(usage),
        "model_calls": sum(1 for call in run["provider_calls"] if call["operation"] != "embed"),
    }


# ---- Terminal output (personal data; for the reviewer only) ------------------------


def _print_profile(run: dict[str, Any], checks: dict[str, Any]) -> None:
    profile = run["confirmed_profile"] or run["draft_profile"]
    labels = record_labels(profile)
    out("", "=" * 100, "EXTRACTED PROFILE (personal data - terminal only)", "=" * 100)
    out(f"contact: {json.dumps(profile['contact'], ensure_ascii=False)}")
    for record in profile["records"]:
        dates = " - ".join(d for d in (record["start_date"], record["end_date"]) if d)
        header = [record["title"], record["organization"], dates, record["location"]]
        flag = " [NEEDS REVIEW]" if record["needs_review"] else ""
        span = "span" if record["source_ref"] else "NO SPAN"
        out(
            "",
            f"[{labels[record['record_id']]}] "
            + " | ".join(p for p in header if p)
            + f"  ({span}){flag}",
        )
        for reason in record["review_reasons"]:
            out(f"    review: {reason}")
        if record["summary"]:
            summary = squash(record["summary"])
            out(
                f"    summary ({len(summary)} chars): {summary[:260]}"
                + ("..." if len(summary) > 260 else "")
            )
        for bullet in record["bullets"]:
            mark = "!" if bullet["needs_review"] else "-"
            text = squash(bullet["text"])
            out(f"    {mark} {text[:200]}" + ("..." if len(text) > 200 else ""))
            for reason in bullet["review_reasons"]:
                out(f"        review: {reason}")
        if record["skills"]:
            out(f"    skills ({len(record['skills'])}): {', '.join(record['skills'])}")
    for conflict in profile["conflicts"]:
        values = " | ".join(value["value"] for value in conflict["values"])
        out(
            f"conflict [{conflict['field']}, {conflict['resolution']}]: "
            f"{conflict['description']} -> {values}"
        )
    coverage = checks["line_coverage"]
    out("", f"SOURCE LINES NOT FOUND IN THE PROFILE ({coverage['uncovered']}):")
    for line in coverage["uncovered_lines"]:
        out(f"    {line[:160]}" + ("..." if len(line) > 160 else ""))


def _print_draft(entry: dict[str, Any], checks: dict[str, Any], run: dict[str, Any]) -> None:
    generation, labels = entry["generation"], record_labels(run["confirmed_profile"])
    home = {
        evidence_id: labels.get((item["body"].get("parent") or {}).get("record_id") or "", "-")
        for evidence_id, item in entry["evidence"].items()
        if item["body"]
    }

    def line(claim: dict[str, Any], indent: str) -> str:
        cites = ", ".join(dict.fromkeys(home.get(key, "?") for key in claim["evidence_ids"]))
        notes = f"  !! {' '.join(claim['warnings'])}" if claim["warnings"] else ""
        return f"{indent}[{claim['validation_status']}] {claim['text']}  <{cites}>{notes}"

    resume = generation["resume"]
    posting = entry["posting"]
    out(
        "",
        "=" * 100,
        f"DRAFT for {posting['title']} at {posting['company']} ({posting['fit']} fit) "
        "- personal data, terminal only",
        "=" * 100,
    )
    out(
        f"model {generation['model']}; usage {generation['usage']}; "
        f"retrieved {len(generation['retrieved_evidence_ids'])}"
    )
    out("SUMMARY", *(line(claim, "  ") for claim in resume["summary"]))
    for section in SECTIONS_WITH_ENTRIES:
        out(section.upper())
        for item in resume[section]:
            header = [item["heading"], item["subheading"], item["date_range"], item["location"]]
            out(f"  [{labels.get(item['record_id'], '?')}] " + " | ".join(p for p in header if p))
            out(*(line(claim, "    - ") for claim in item["bullets"]))
    out("SKILLS: " + ", ".join(claim["text"] for claim in resume["skills"]))
    out("COVER LETTER", *(line(c, "  ") for c in generation["cover_letter"]["paragraphs"]))
    counts = generation["coverage_summary"]
    out(f"COVERAGE: {counts}")
    for item in generation["coverage"]:
        cites = ", ".join(dict.fromkeys(home.get(key, "?") for key in item["evidence_ids"]))
        out(
            f"  [{item['status']}] ({item['importance']}) {item['requirement_text']}",
            f"        -> {item['rationale']}  <{cites}>",
        )
    out("OMITTED CLAIMS")
    for omitted in generation["omitted_claims"]:
        out(f"  - {omitted['section']}: {omitted['text']} -- {omitted['reason']}")
    out("DRAFT WARNINGS", *(f"  - {warning}" for warning in generation["warnings"]))
    out("NUMBERS NOT IN CITED EVIDENCE")
    for problem in checks["numbers"]["problems"]:
        out(
            f"  - {problem['section']} [{problem['status']}] {problem['numbers']}: "
            f"{problem['text']}"
        )
    emphasis = {
        name: value
        for name, value in checks["emphasis"].items()
        if not name.endswith("_ids") and name != "skill_names"
    }
    out(f"EMPHASIS: {json.dumps(emphasis)}")
    out(f"GAP CHECK: employment months {checks['gaps']['employment_months']}; lacking:")
    for gap in checks["gaps"]["lacking"]:
        out(f"  - [{gap['status']}] {gap['text']} ({'; '.join(gap['rules'])})")
    out(f"QUALITY: {json.dumps(checks['quality'])}")


def print_terminal(run: dict[str, Any], analysis: dict[str, Any]) -> None:
    if analysis["extraction"]:
        _print_profile(run, analysis["extraction"])
    for entry, checks in zip(run["jobs"], analysis["jobs"], strict=True):
        if checks["state"] == "completed":
            _print_draft(entry, checks, run)


# ---- The report (no personal data) -------------------------------------------------


def _flag(passed: bool) -> str:
    return "PASS" if passed else "FAIL"


def _percent(share: float | None) -> str:
    return "n/a" if share is None else f"{100 * share:.1f}%"


def _table(header: list[str], rows: list[list[Any]]) -> list[str]:
    def cell(value: Any) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ")

    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(cell(value) for value in row) + " |" for row in rows]
    return lines


def _report_extraction(checks: dict[str, Any] | None, index: dict[str, Any] | None) -> list[str]:
    lines = ["## 2. Extraction of the master file", ""]
    if checks is None:
        return [*lines, "Extraction did not complete; see the failures above.", ""]
    coverage = checks["line_coverage"]
    content_lines = coverage["lines"] - coverage["section_labels"]
    found = content_lines - coverage["uncovered"]
    lines += _table(
        ["Measure", "Value"],
        [
            ["Master text", f"{checks['source_chars']} characters, {checks['source_words']} words"],
            ["Records in the draft profile", checks["records"]],
            ["Records by category", json.dumps(checks["records_by_category"], sort_keys=True)],
            ["Records the model returned (before merging)", checks["model_records"]],
            ["Statements (bullets)", checks["bullets"]],
            ["Records with a summary paragraph", checks["summaries"]],
            ["Skills listed", checks["skills"]],
            [
                "Records with a located source span",
                f"{checks['records_with_span']} of {checks['records']}",
            ],
            [
                "Statements with a located source span",
                f"{checks['bullets_with_span']} of {checks['bullets']}",
            ],
            [
                "Share of records and statements whose span equals the original text slice",
                f"{checks['spans_exact']} of {checks['span_items']} "
                f"({_percent(checks['span_exact_share'])})",
            ],
            [
                "Header fields (title, organisation, location, dates) found verbatim in the master",
                f"{checks['header_fields_verbatim_in_source']} of {checks['header_fields']}",
            ],
            [
                "Summary paragraphs found verbatim in the master",
                f"{checks['summaries_verbatim_in_source']} of {checks['summaries']}",
            ],
            ["Records flagged for review", checks["records_needing_review"]],
            ["Statements flagged for review", checks["bullets_needing_review"]],
            [
                "Employment or education records without dates",
                checks["dated_records_without_dates"],
            ],
            [
                "Conflicts reported (unresolved before review)",
                f"{checks['conflicts']} ({checks['unresolved_conflicts']})",
            ],
            ["Contact fields found", ", ".join(checks["contact_fields_found"]) or "none"],
            [
                "Master lines with content found in the profile",
                f"{found} of {content_lines} (by span {coverage['covered_by_span']}, by text "
                f"{coverage['covered_by_text']}, as a header fact "
                f"{coverage['covered_by_header_fact']}, as a contact detail "
                f"{coverage['covered_as_contact']}, as skills {coverage['covered_as_skills']}; "
                f"{coverage['section_labels']} section-label lines not counted)",
            ],
            ["Master lines with content not found in the profile", coverage["uncovered"]],
            [
                "Words in extracted statements and summaries / words in the master",
                f"{checks['statement_words']} / {checks['source_words']}",
            ],
        ],
    )
    if checks["review_reason_kinds"]:
        lines += ["", "Review reasons by kind (quoted terms and figures removed):", ""]
        lines += _table(
            ["Reason", "Count"],
            [[kind, count] for kind, count in sorted(checks["review_reason_kinds"].items())],
        )
    if index:
        lines += [
            "",
            f"Confirmation: profile status `{index['status']}`, index state "
            f"`{index['index_state']}`, {index['embedded']} of {index['chunks']} evidence "
            "records embedded.",
        ]
    return [*lines, ""]


def _report_flags(jobs: list[dict[str, Any]]) -> list[str]:
    """One row per job: how far it got and the result of each flag."""
    if not jobs:
        return ["No job was run.", ""]
    rows = []
    for checks in jobs:
        posting = checks["posting"]
        row = [posting["fit"], f"{posting['title']}, {posting['company']}", checks["state"]]
        if checks["state"] == "completed":
            gaps, coverage = checks["gaps"], checks["coverage"]
            row += [
                _flag(checks["facts"]["passed"]),
                _flag(checks["numbers"]["passed"]),
                _flag(checks["evidence"]["passed"]),
                _flag(gaps["passed"]) if gaps["lacking"] else "n/a",
                _flag(gaps["nothing_invented"]),
                f"{coverage['percent']}% (S{coverage['supported']} P{coverage['partial']} "
                f"M{coverage['missing']} U{coverage['uncertain']})",
            ]
        else:
            row += ["-"] * 6
        rows.append(row)
    header = [
        "Fit",
        "Posting",
        "Outcome",
        "Facts preserved",
        "Numbers in evidence",
        "Evidence opens",
        "Lacking qualifications not rated supported",
        "No absent term claimed",
        "Evidence coverage",
    ]
    return [*_table(header, rows), ""]


def _report_job(number: int, checks: dict[str, Any]) -> list[str]:
    posting = checks["posting"]
    kind = "synthetic fixture" if posting["synthetic"] else "live public posting"
    lines = [
        f"### 3.{number} {posting['title']}, {posting['company']} ({posting['fit']} fit)",
        "",
        f"- Posting: {kind}, slug `{posting['slug']}`, {posting['description_chars']} characters"
        + (f", retrieved {posting['date_retrieved']}" if posting["date_retrieved"] else ""),
    ]
    if posting["url"]:
        lines.append(f"- Public URL: <{posting['url']}>")
    lines.append(f"- Outcome: **{checks['state']}**")
    if "job" not in checks:
        return [*lines, ""]
    job = checks["job"]
    lines.append(
        f"- Job analysis: {job['requirements']} requirements "
        f"({json.dumps(job['by_importance'], sort_keys=True)}; {job['inferred']} inferred); "
        f"{job['with_located_quote']} with a located quote, {job['quotes_exact']} of them equal "
        "to the posting's text slice"
    )
    if checks["state"] != "completed":
        return [*lines, ""]
    draft, facts, numbers = checks["draft"], checks["facts"], checks["numbers"]
    evidence, coverage, gaps = checks["evidence"], checks["coverage"], checks["gaps"]
    quality = checks["quality"]
    lines += [
        f"- Draft: provider `{draft['provider_mode']}`, model `{draft['model']}`, "
        f"{quality['generation_calls']} generation call(s), {draft['retrieved']} evidence "
        f"records retrieved, usage {json.dumps(draft['usage'], sort_keys=True)}",
        "",
    ]
    lines += _table(
        ["Check", "Result", "Detail"],
        [
            [
                "Fact preservation: every header equals the confirmed record",
                _flag(facts["passed"]),
                f"{facts['entries']} entries; {len(facts['problems'])} problem(s)"
                + ("".join(f"; {problem}" for problem in facts["problems"])),
            ],
            [
                "Every number in a statement occurs in its cited evidence",
                _flag(numbers["passed"]),
                f"{numbers['claims_with_numbers']} statements with numbers, "
                f"{numbers['numbers_checked']} numbers, "
                f"{numbers['numbers_not_in_cited_evidence']} not in the cited evidence"
                + "".join(
                    f"; {p['section']} ({p['status']}, "
                    f"{'cites evidence' if p['cites_evidence'] else 'cites nothing'}"
                    f"{', figure is in the posting' if p['all_in_posting'] else ''})"
                    for p in numbers["problems"]
                ),
            ],
            [
                "Every cited evidence ID resolves through GET /api/evidence",
                _flag(evidence["passed"]),
                f"{evidence['resolved']} of {evidence['referenced']} opened; "
                f"{evidence['with_source_offsets']} carry source offsets, "
                f"{evidence['excerpt_not_the_source_slice']} of those differ from the source slice",
            ],
            [
                "Coverage summary equals the recount of its items",
                _flag(coverage["summary_matches_items"]),
                f"supported {coverage['supported']}, partial {coverage['partial']}, missing "
                f"{coverage['missing']}, uncertain {coverage['uncertain']}; assessed "
                f"{coverage['assessed']}; percent {coverage['percent']}",
            ],
            [
                "Explicit qualifications the profile lacks are not rated supported",
                _flag(gaps["passed"]) if gaps["lacking"] else "n/a",
                f"{len(gaps['lacking'])} requirement(s) caught by the rule, "
                f"{gaps['lacking_rated_supported']} rated supported",
            ],
            [
                "No absent technology is claimed in the resume or a cited letter paragraph",
                _flag(gaps["nothing_invented"]),
                "posting terms absent from the master: "
                + (", ".join(gaps["absent_terms_named_by_posting"]) or "none")
                + "; claimed: "
                + (", ".join(gaps["absent_terms_claimed"]) or "none"),
            ],
        ],
    )
    lines += ["", "Statements by validation status:", ""]
    statuses = sorted(
        {s for counts in draft["validation_status_by_section"].values() for s in counts}
    )
    lines += _table(
        ["Section", *statuses],
        [
            [section, *(counts.get(status, 0) for status in statuses)]
            for section, counts in draft["validation_status_by_section"].items()
        ]
        + [["all", *(draft["validation_status"].get(status, 0) for status in statuses)]],
    )
    lines += [
        "",
        f"Omitted claims: {draft['omitted']} "
        f"(by section {json.dumps(draft['omitted_by_section'], sort_keys=True)}).",
    ]
    if draft["omitted_reason_kinds"]:
        lines += [
            "",
            *_table(
                ["Reason for omission (quoted terms and figures removed)", "Count"],
                [[kind, count] for kind, count in sorted(draft["omitted_reason_kinds"].items())],
            ),
        ]
    if draft["warning_kinds"]:
        lines += [
            "",
            *_table(
                ["Warning on a kept statement (quoted terms and figures removed)", "Count"],
                [[kind, count] for kind, count in sorted(draft["warning_kinds"].items())],
            ),
        ]
    if draft["draft_warnings"]:
        lines += ["", "Draft-level warnings: " + " / ".join(draft["draft_warnings"])]
    lines += ["", "Requirements and their ratings (text from the public posting):", ""]
    lines += _table(
        ["Status", "Importance", "Cited", "Requirement"],
        [[r["status"], r["importance"], r["cited"], r["text"]] for r in coverage["requirements"]],
    )
    gaps_listed = [r for r in coverage["requirements"] if r["status"] in GAP_STATUSES]
    lines += [
        "",
        f"Rated missing or uncertain: {len(gaps_listed)} of {len(coverage['requirements'])}.",
    ]
    if gaps["lacking"]:
        lines += ["", "Requirements the rule identifies as lacking in the profile:", ""]
        lines += _table(
            ["Rated", "Importance", "Requirement", "Why it counts as lacking"],
            [
                [g["status"], g["importance"], g["text"], "; ".join(g["rules"])]
                for g in gaps["lacking"]
            ],
        )
    lines += ["", "Shape of the documents (descriptive, not a score):", ""]
    lines += _table(
        ["Measure", "Value"],
        [
            ["Resume words (headers, summary, bullets, skills)", quality["resume_words"]],
            [
                "Summary",
                f"{quality['summary_sentences']} statement(s), {quality['summary_words']} words",
            ],
            ["Experience and project bullets", quality["bullets"]],
            [
                "Bullets the model wrote in the draft the server kept",
                quality["model_bullets_in_kept_draft"],
            ],
            [
                "Words per bullet (mean / max)",
                f"{quality['bullet_words_mean']} / {quality['bullet_words_max']}",
            ],
            ["Bullets over 30 words", quality["bullets_over_30_words"]],
            [
                "Bullets starting with a past-tense verb (heuristic)",
                f"{quality['bullets_starting_with_verb']} of {quality['bullets']}",
            ],
            ["Bullets in the first person", quality["bullets_in_first_person"]],
            ["Bullets ending with a full stop", quality["bullets_ending_with_full_stop"]],
            ["Exact duplicate statements", quality["exact_duplicate_statements"]],
            [
                f"Near-duplicate pairs (word overlap >= {NEAR_DUPLICATE_JACCARD})",
                quality["near_duplicate_pairs"],
            ],
            [
                "Evidence records cited by more than one bullet",
                quality["evidence_cited_by_several_bullets"],
            ],
            ["Skills listed", quality["skills"]],
            [
                "Cover letter",
                f"{quality['cover_letter_paragraphs']} paragraphs, "
                f"{quality['cover_letter_words']} words, "
                f"{quality['cover_letter_paragraphs_citing_evidence']} citing "
                "evidence",
            ],
            [
                "Profile records shown, by category (in profile / shown / shown without bullets)",
                "; ".join(
                    f"{category}: {c['in_profile']} / {c['shown']} / {c['shown_without_bullets']}"
                    for category, c in sorted(quality["records_by_category"].items())
                ),
            ],
            [
                "Correction pass",
                "not used"
                if quality["generation_calls"] < 2
                else f"used, with {quality['correction_pass_feedback_items']} finding(s); "
                f"the server kept draft {quality['kept_draft']}",
            ],
            ["Coverage ratings the server lowered", quality["coverage_ratings_lowered_by_server"]],
            [
                "Coverage rationales the server replaced",
                quality["coverage_rationales_replaced_by_server"],
            ],
        ],
    )
    if quality["correction_pass_feedback_kinds"]:
        lines += ["", "Findings on the first draft that were sent to the correction pass:", ""]
        lines += _table(
            ["Section and finding (quoted terms and figures removed)", "Count"],
            [
                [kind, count]
                for kind, count in sorted(quality["correction_pass_feedback_kinds"].items())
            ],
        )
    return [*lines, ""]


def _report_emphasis(analysis: dict[str, Any]) -> list[str]:
    done = [job for job in analysis["jobs"] if job["state"] == "completed"]
    lines = ["## 4. Emphasis across the jobs", ""]
    if len(done) < 2:
        return [*lines, "Fewer than two drafts completed, so there is nothing to compare.", ""]
    lines += [
        "Records are named by category and position in the profile, never by name. "
        "Experience entries always appear in profile order (the application lists every "
        "confirmed role), so emphasis shows in the number of bullets per record, in the "
        "order of the project section and in what the summary and the letter cite.",
        "",
    ]
    lines += _table(
        [
            "Pair",
            "Jaccard: evidence cited by resume statements",
            "Jaccard: evidence retrieved",
            "Jaccard: skills listed",
            "Same skill order",
        ],
        [
            [
                " vs ".join(pair["jobs"]),
                pair["cited_evidence_jaccard"],
                pair["retrieved_evidence_jaccard"],
                pair["skills_jaccard"],
                "yes" if pair["same_skill_order"] else "no",
            ]
            for pair in analysis["emphasis_pairs"]
        ],
    )
    records = list(
        dict.fromkeys(label for job in done for label in job["emphasis"]["bullets_per_record"])
    )
    lines += ["", "Bullets per record:", ""]
    lines += _table(
        ["Record", *(job["posting"]["slug"] for job in done)],
        [
            [
                label,
                *(job["emphasis"]["bullets_per_record"].get(label, "not shown") for job in done),
            ]
            for label in records
        ],
    )
    lines += ["", "Evidence retrieved per record:", ""]
    retrieved = list(
        dict.fromkeys(label for job in done for label in job["emphasis"]["retrieved_per_record"])
    )
    lines += _table(
        ["Record", *(job["posting"]["slug"] for job in done)],
        [
            [label, *(job["emphasis"]["retrieved_per_record"].get(label, 0) for job in done)]
            for label in sorted(retrieved)
        ],
    )
    lines += ["", "What leads:", ""]
    lines += _table(
        [
            "Job",
            "Most bullets",
            "Project section order",
            "Summary cites",
            "First letter body paragraph cites",
        ],
        [
            [
                job["posting"]["slug"],
                ", ".join(job["emphasis"]["records_with_most_bullets"]) or "-",
                ", ".join(job["emphasis"]["project_section_order"]) or "-",
                ", ".join(job["emphasis"]["summary_cites"]) or "-",
                ", ".join(job["emphasis"]["first_letter_body_cites"]) or "-",
            ]
            for job in done
        ],
    )
    return [*lines, ""]


def render_report(run: dict[str, Any], analysis: dict[str, Any], master_name: str) -> str:
    """The report file: aggregate numbers, flags and public posting text only."""
    settings, code = run["settings"], run.get("code", {})
    selftest = run["mode"] == "selftest"
    lines = [
        "# Experience master evaluation report",
        "",
        "Generated by `backend/scripts/evaluate_experience_master.py` "
        f"on {run['started_at']} (America/New_York).",
        "",
    ]
    if selftest:
        lines += [
            "> **SELF-TEST.** Fake provider and fictional fixtures. This checks the script, "
            "not the application's real output. It is not an evaluation result.",
            "",
        ]
    lines += [
        "This file holds aggregate numbers, pass/fail flags and text from public job "
        "postings only. It contains no name, employer, contact detail or sentence from the "
        "master file; records are referred to by category and position.",
        "",
        "## 1. Run",
        "",
    ]
    cleanup = run["cleanup"] or {}
    remaining = run.get("remaining_documents") or {}
    spent = run.get("requests_spent", {})
    lines += _table(
        ["Item", "Value"],
        [
            [
                "Input",
                f"`{master_name}`, submitted as "
                + ", ".join(f'"{source["label"]}"' for source in run["sources"]),
            ],
            [
                "Application code",
                f"commit `{code.get('commit')}` with "
                f"{code.get('uncommitted_files_in_backend_app')} uncommitted file(s) under "
                f"`backend/app`; prompt version `{code.get('prompt_version')}`",
            ],
            [
                "Provider",
                f"`{settings['provider_mode']}`, generation model "
                f"`{settings['generation_model']}`, embedding model "
                f"`{settings['embedding_model']}`, reasoning effort "
                f"`{settings['reasoning_effort'] or 'not sent'}`",
            ],
            [
                "Provider limits",
                f"timeout {settings['provider_timeout_seconds']} s per call, "
                f"{settings['provider_max_retries']} SDK retries allowed, "
                f"semantic verifier "
                f"{'on' if settings['semantic_verifier'] else 'off'}",
            ],
            [
                "Retrieval",
                f"{settings['retrieval_per_requirement']} candidates per requirement, "
                f"at most {settings['retrieval_max_context']} evidence records, "
                f"token budget {settings['retrieval_token_budget']}",
            ],
            [
                "Billable requests made",
                ", ".join(
                    f"{name} {spent.get(name, 0)}/{limit}" for name, limit in REQUEST_BUDGET.items()
                ),
            ],
            [
                "Provider calls",
                f"{analysis['model_calls']} model calls, "
                f"{sum(1 for c in run['provider_calls'] if c['operation'] == 'embed')}"
                f" embedding calls, {run.get('sdk_retries', 0)} SDK retries seen",
            ],
            ["Wall time", f"{run.get('wall_seconds')} s"],
            ["Failures", len(run["failures"])],
            [
                "Session deleted",
                f"counts {json.dumps(cleanup.get('deleted_counts'))}; token "
                f"rejected afterwards: {cleanup.get('token_rejected_afterwards')}",
            ],
            ["Documents left after deletion", json.dumps(remaining, sort_keys=True)],
            ["Test database dropped", run.get("database_dropped")],
        ],
    )
    lines.append("")
    if run["failures"]:
        lines += ["Failures, exactly as the application reported them:", ""]
        lines += [f"- {failure}" for failure in run["failures"]]
        lines.append("")
    lines += _report_extraction(analysis["extraction"], analysis["index"])
    lines += ["## 3. Jobs", ""]
    lines += _report_flags(analysis["jobs"])
    for number, checks in enumerate(analysis["jobs"], start=1):
        lines += _report_job(number, checks)
    lines += _report_emphasis(analysis)
    lines += ["## 5. Latency and token usage", "", "HTTP steps:", ""]
    lines += _table(
        ["Step", "Request", "Status", "Seconds"],
        [[s["step"], f"`{s['request']}`", s["status"], s["seconds"]] for s in run["steps"]],
    )
    lines += ["", "Provider calls:", ""]
    lines += _table(
        [
            "Step",
            "Operation",
            "Seconds",
            "Input tokens",
            "Output tokens",
            "Embedding tokens",
            "API requests",
            "Error",
        ],
        [
            [
                c["step"],
                c["operation"],
                c["seconds"],
                c["input_tokens"],
                c["output_tokens"],
                c["embedding_tokens"],
                c["api_requests"],
                c["error"] or "",
            ]
            for c in run["provider_calls"]
        ],
    )
    total = analysis["usage_total"]
    lines += [
        "",
        f"Total: {total.get('input_tokens', 0)} input tokens, {total.get('output_tokens', 0)} "
        f"output tokens, {total.get('embedding_tokens', 0)} embedding tokens in "
        f"{total.get('api_requests', 0)} API requests. Prices are not computed here.",
        "",
        "## 6. What these checks do and do not show",
        "",
        "- The flags are deterministic comparisons of the API's answers with the master text "
        "and the confirmed profile. They show that headers, numbers and citations are "
        "consistent; they do not show that a sentence is a fair reading of its evidence.",
        "- The lacking-qualification rule is narrow on purpose (years asked versus employment "
        "dates, a degree asked with no education record, named terms absent from the master "
        "text). A requirement it does not catch can still be a gap.",
        "- Whether the documents read well and are tailored is a human judgement made from the "
        "terminal output, which is not stored here.",
        "- Model output varies from run to run; this is one run.",
        "",
    ]
    return "\n".join(lines)


def privacy_violations(report: str, run: dict[str, Any]) -> list[str]:
    """Master-file content found in ``report``: any run of four consecutive
    words of the master text, and any contact value, record title or
    organisation name of the profile. Text that is also in a posting (a public
    document) or in the application's own messages is not counted.
    """

    def shingles(text: str, size: int = 4) -> set[tuple[str, ...]]:
        tokens = words(text)
        return {tuple(tokens[i : i + size]) for i in range(len(tokens) - size + 1)}

    public_text = "\n".join(
        [entry["description"] for entry in run["jobs"]]
        + [json.dumps(entry["posting"]) for entry in run["jobs"]]
        + [
            requirement["text"]
            for entry in run["jobs"]
            if entry["job"]
            for requirement in entry["job"]["requirements"]
        ]
    )
    private = shingles(master_text(run)) - shingles(public_text)
    found = [" ".join(shingle) for shingle in shingles(report) if shingle in private]

    profile = run["confirmed_profile"] or run["draft_profile"] or {"contact": {}, "records": []}
    names = [value for value in profile["contact"].values() if isinstance(value, str)]
    names += profile["contact"].get("links", [])
    for record in profile["records"]:
        if record["category"] != "skill":
            names += [record["title"], record["organization"] or ""]
    folded_report, folded_public = squash(report).lower(), squash(public_text).lower()
    for name in names:
        folded = squash(name).lower()
        if len(folded) >= 6 and folded in folded_report and folded not in folded_public:
            found.append(name)
    return sorted(set(found))


def log_violations(run: dict[str, Any]) -> int:
    """Log lines of the run that hold four consecutive words of the master text."""
    tokens = words(master_text(run))
    private = {" ".join(tokens[i : i + 4]) for i in range(len(tokens) - 3)}
    count = 0
    for line in run.get("log_lines", []):
        line_tokens = words(line)
        grams = {" ".join(line_tokens[i : i + 4]) for i in range(len(line_tokens) - 3)}
        count += bool(grams & private)
    return count


# ---- Entry point -------------------------------------------------------------------


def _inside_repository(path: Path) -> bool:
    return path.resolve().is_relative_to(REPO_ROOT)


def _save_transcript(run: dict[str, Any], directory: Path) -> None:
    """The raw run, personal data included, for reading model output afterwards
    and for MASTER_EVAL_REPLAY. Never inside the repository."""
    directory.mkdir(parents=True, exist_ok=True)
    for name, content in (
        ("run.json", {key: value for key, value in run.items() if key != "raw_calls"}),
        ("provider_calls.json", run["raw_calls"]),
    ):
        path = directory / name
        text = json.dumps(content, ensure_ascii=False, indent=1, default=str)
        path.write_text(text, encoding="utf-8")
        path.chmod(0o600)
    out(f"Transcript saved to {directory} (personal data: delete it when you are done).")


def _print_summary(run: dict[str, Any], analysis: dict[str, Any]) -> None:
    out("", "=" * 100, "RESULTS", "=" * 100)
    extraction = analysis["extraction"]
    if extraction:
        out(
            f"extraction: {extraction['records']} records "
            f"{json.dumps(extraction['records_by_category'], sort_keys=True)}, "
            f"{extraction['bullets']} statements, spans exact "
            f"{extraction['spans_exact']}/{extraction['span_items']}, needs review "
            f"{extraction['records_needing_review']} records + "
            f"{extraction['bullets_needing_review']} statements, uncovered source lines "
            f"{extraction['line_coverage']['uncovered']}"
        )
    if analysis["index"]:
        out(f"index: {analysis['index']}")
    for checks in analysis["jobs"]:
        posting = checks["posting"]
        out(f"{posting['fit']:<8} {posting['slug']}: {checks['state']}")
        if checks["state"] != "completed":
            continue
        coverage, draft = checks["coverage"], checks["draft"]
        out(
            f"    facts {_flag(checks['facts']['passed'])} | numbers "
            f"{_flag(checks['numbers']['passed'])} | evidence {_flag(checks['evidence']['passed'])}"
            f" ({checks['evidence']['resolved']}/{checks['evidence']['referenced']}) | lacking "
            f"qualifications not supported "
            f"{_flag(checks['gaps']['passed']) if checks['gaps']['lacking'] else 'n/a'} "
            f"({len(checks['gaps']['lacking'])} caught) | nothing invented "
            f"{_flag(checks['gaps']['nothing_invented'])}"
        )
        out(
            f"    statuses {draft['validation_status']} | omitted {draft['omitted']} | coverage "
            f"S{coverage['supported']} P{coverage['partial']} M{coverage['missing']} "
            f"U{coverage['uncertain']} = {coverage['percent']}%"
        )
    for pair in analysis["emphasis_pairs"]:
        out(
            f"emphasis {' vs '.join(pair['jobs'])}: cited {pair['cited_evidence_jaccard']}, "
            f"retrieved {pair['retrieved_evidence_jaccard']}, skills {pair['skills_jaccard']}"
        )
    out("provider calls:")
    for call in run["provider_calls"]:
        out(
            f"    {call['step']:<52}{call['operation']:<20}{call['seconds']:>8.1f}s "
            f"in {call['input_tokens']:>6} out {call['output_tokens']:>6} "
            f"emb {call['embedding_tokens']:>6} {call['error'] or ''}"
        )
    out(
        f"usage total: {analysis['usage_total']}; SDK retries seen: {run.get('sdk_retries')}; "
        f"wall time {run.get('wall_seconds')} s"
    )
    out(
        f"log lines captured: {len(run.get('log_lines', []))}; lines with master text: "
        f"{log_violations(run)}"
    )
    out(
        f"cleanup: {run['cleanup']}; documents left {run.get('remaining_documents')}; "
        f"database dropped {run.get('database_dropped')}"
    )
    out(f"failures ({len(run['failures'])}):", *(f"    {failure}" for failure in run["failures"]))


def main() -> int:
    selftest = os.environ.get(SELFTEST_VARIABLE) == "1"
    replay = os.environ.get(REPLAY_VARIABLE)
    transcript = os.environ.get(TRANSCRIPT_VARIABLE)
    report_path = Path(os.environ.get(REPORT_PATH_VARIABLE) or DEFAULT_REPORT_PATH).expanduser()
    master_path = Path(os.environ.get(MASTER_PATH_VARIABLE) or DEFAULT_MASTER_PATH).expanduser()
    try:
        if transcript and _inside_repository(Path(transcript)):
            raise EvaluationError(
                f"{TRANSCRIPT_VARIABLE} must be a folder outside the repository: the transcript "
                "holds the master file's content."
            )
        if replay:
            run = json.loads(Path(replay).read_text(encoding="utf-8"))
            out(f"REPLAY of {replay}: nothing is called, checks and report are recomputed.")
        elif selftest:
            out("SELF-TEST: fake provider, fictional fixtures. Not an evaluation result.")
            run = run_evaluation(_selftest_sources(), _selftest_postings(), selftest=True)
        else:
            source_text = read_master(master_path)
            postings = select_postings(os.environ.get(JOBS_VARIABLE))
            out(
                f"Master file: {master_path.name}, {len(source_text)} characters, "
                f"{len(source_text.splitlines())} lines"
            )
            out("Postings: " + "; ".join(f"{p.fit}: {p.slug}" for p in postings))
            sources = [{"label": SOURCE_LABEL, "source_type": "resume", "text": source_text}]
            run = run_evaluation(sources, postings, selftest=False)
            run["master_name"] = master_path.name
    except EvaluationError as error:
        out(f"ERROR: {error}")
        return 2

    if transcript and not replay:
        _save_transcript(run, Path(transcript))
    analysis = analyse(run)
    print_terminal(run, analysis)
    _print_summary(run, analysis)

    report = render_report(run, analysis, run.get("master_name", "fictional sample profile"))
    violations = privacy_violations(report, run)
    if run["mode"] == "selftest":
        out("", "-" * 100, "REPORT (self-test: printed, not written)", "-" * 100, report)
        out(f"privacy check of the report: {len(violations)} finding(s) {violations[:10]}")
        return 1 if run["failures"] or violations else 0
    if violations:
        out(
            "",
            "The report was NOT written: it would contain master-file content:",
            *(f"    {violation}" for violation in violations[:40]),
        )
        return 3
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    out("", f"Report written to {report_path} (no personal data; privacy check passed).")
    return 1 if run["failures"] else 0


if __name__ == "__main__":
    sys.exit(main())

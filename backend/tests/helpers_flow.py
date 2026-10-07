"""Helpers for tests that drive the whole pipeline through the HTTP API with
the fictional fixtures in ``backend/fixtures``.

The profile helpers (``sample_sources``, ``ingest``, ``confirmed_profile``,
``patch_body``) live at the top of tests/integration/test_profile_flow.py;
this module adds the job, generation and evidence steps.
"""

import json
from typing import Any

import httpx

from tests.helpers_generation import all_claims
from tests.integration.test_profile_flow import fixture_text, source

# Every route that needs a session, with harmless placeholder IDs and bodies.
# Used to prove that a missing, expired or revoked token is refused everywhere.
PROTECTED_REQUESTS: list[tuple[str, str, dict[str, Any] | None]] = [
    ("GET", "/api/session", None),
    ("DELETE", "/api/session", None),
    ("POST", "/api/profiles/ingest", {"sources": []}),
    ("GET", "/api/profile", None),
    ("PATCH", "/api/profile", {"expected_version": 1, "contact": {}, "records": []}),
    ("POST", "/api/profile/confirm", {"expected_version": 1}),
    ("GET", "/api/evidence/some-evidence-id", None),
    ("POST", "/api/jobs", {"description": "Any job."}),
    ("GET", "/api/jobs", None),
    ("GET", "/api/jobs/some-job-id", None),
    ("PATCH", "/api/jobs/some-job-id", {"expected_version": 1, "requirements": []}),
    ("POST", "/api/generations", {"job_id": "some-job-id"}),
    ("GET", "/api/generations", None),
    ("GET", "/api/generations/some-generation-id", None),
    ("PATCH", "/api/generations/some-generation-id", {"expected_revision": 1}),
    ("POST", "/api/generations/some-generation-id/validate", None),
    ("POST", "/api/generations/some-generation-id/items/some-item-id/regenerate", None),
]


def synthetic_job(slug: str) -> dict[str, Any]:
    """One invented job posting from fixtures/jobs/synthetic, with its
    ``expected`` block."""
    return json.loads(fixture_text(f"jobs/synthetic/{slug}.json"))


def injection_sources() -> list[dict[str, str]]:
    """The second fictional candidate, whose resume holds a planted instruction."""
    return [source("Resume", "resume", fixture_text("profiles/injection_resume.txt"))]


def keyed(headers: dict[str, str], key: str) -> dict[str, str]:
    """The session headers plus an Idempotency-Key."""
    return {**headers, "Idempotency-Key": key}


async def analysed_job(
    client: httpx.AsyncClient, headers: dict[str, str], slug: str
) -> dict[str, Any]:
    """POST /api/jobs with a synthetic posting; returns the analysed job."""
    posting = synthetic_job(slug)
    body = {name: posting[name] for name in ("description", "title", "company")}
    response = await client.post("/api/jobs", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


async def post_generation(
    client: httpx.AsyncClient, headers: dict[str, str], job_id: str, key: str
) -> httpx.Response:
    return await client.post(
        "/api/generations", json={"job_id": job_id}, headers=keyed(headers, key)
    )


async def generated(
    client: httpx.AsyncClient, headers: dict[str, str], job_id: str, key: str
) -> dict[str, Any]:
    """A new draft for the job (201)."""
    response = await post_generation(client, headers, job_id, key)
    assert response.status_code == 201, response.text
    return response.json()


def requirement_inputs(job: dict[str, Any]) -> list[dict[str, Any]]:
    """The PATCH /api/jobs/{id} form of the job's requirements, unchanged."""
    return [
        {name: requirement[name] for name in ("requirement_id", "text", "category", "importance")}
        for requirement in job["requirements"]
    ]


def cited_evidence_ids(generation: dict[str, Any]) -> list[str]:
    """Every evidence ID a Generation response refers to: the retrieved
    context, each statement's citations and each coverage item's citations."""
    evidence_ids = list(generation["retrieved_evidence_ids"])
    for cited in [*all_claims(generation), *generation["coverage"]]:
        evidence_ids.extend(cited["evidence_ids"])
    return list(dict.fromkeys(evidence_ids))


def find_bullet(generation: dict[str, Any], section: str, fragment: str) -> dict[str, Any]:
    """The one bullet of a resume section whose text contains ``fragment``."""
    matches = [
        bullet
        for entry in generation["resume"][section]
        for bullet in entry["bullets"]
        if fragment in bullet["text"]
    ]
    assert len(matches) == 1, f"{fragment!r} matches {len(matches)} bullets in {section}"
    return matches[0]


def find_item(generation: dict[str, Any], item_id: str) -> dict[str, Any]:
    """The statement with this item ID, wherever it sits in the draft."""
    matches = [claim for claim in all_claims(generation) if claim["item_id"] == item_id]
    assert len(matches) == 1, f"{item_id!r} matches {len(matches)} statements"
    return matches[0]

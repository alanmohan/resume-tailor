"""Working on a finished draft: manual edits, revalidation, coverage
corrections, single-item regeneration and isolation between sessions."""

from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from fastapi import FastAPI

from app.config import Settings
from app.db import Database
from app.providers.base import LLMRegenResult, ProviderTimeout
from app.providers.fake.provider import FakeProvider
from app.repositories import Repositories
from tests.conftest import SessionHandle, client_for
from tests.helpers import assert_error
from tests.helpers_generation import (
    SeededProfile,
    all_claims,
    entry_for,
    install_scripted_provider,
    post_generation,
    seed_job,
    seed_profile,
    with_key,
)

CreateSession = Callable[[], Awaitable[SessionHandle]]

DOCKER_BULLET = "Containerised twelve services with Docker for deployment."
COSTS_BULLET = "Reduced cloud costs by 20% by rightsizing instances."


class Workspace:
    """A session with a finished draft and shortcuts for the routes under test."""

    def __init__(
        self, client: httpx.AsyncClient, session: SessionHandle, seeded: SeededProfile, body: dict
    ) -> None:
        self.client = client
        self.session = session
        self.seeded = seeded
        self.body = body
        self.url = f"/api/generations/{body['generation_id']}"

    def claim(self, text: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        matches = [claim for claim in all_claims(body or self.body) if claim["text"] == text]
        assert len(matches) == 1, f"{text!r}: {len(matches)} claims"
        return matches[0]

    def by_id(self, body: dict[str, Any], item_id: str) -> dict[str, Any]:
        return next(claim for claim in all_claims(body) if claim["item_id"] == item_id)

    async def get(self) -> dict[str, Any]:
        return (await self.client.get(self.url, headers=self.session.headers)).json()

    async def patch(self, **payload: Any) -> httpx.Response:
        return await self.client.patch(self.url, json=payload, headers=self.session.headers)

    async def edit(self, item_id: str, text: str, expected_revision: int) -> dict[str, Any]:
        response = await self.patch(
            expected_revision=expected_revision, edits=[{"item_id": item_id, "text": text}]
        )
        assert response.status_code == 200, response.text
        return response.json()

    async def validate(self) -> httpx.Response:
        return await self.client.post(f"{self.url}/validate", headers=self.session.headers)

    async def regenerate(
        self, item_id: str, key: str = "regen-key-001", **payload: Any
    ) -> httpx.Response:
        return await self.client.post(
            f"{self.url}/items/{item_id}/regenerate",
            json=payload,
            headers=with_key(self.session, key),
        )


async def make_workspace(
    client: httpx.AsyncClient, create_session: CreateSession, repos: Repositories
) -> Workspace:
    session = await create_session()
    seeded = await seed_profile(repos, session)
    job = await seed_job(repos, session)
    response = await post_generation(client, session, job)
    assert response.status_code == 201, response.text
    return Workspace(client, session, seeded, response.json())


# ---- manual edits ------------------------------------------------------------------


async def test_manual_edit_marks_the_item_user_edited_and_needs_revalidation(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    repos: Repositories,
    fake_provider: FakeProvider,
) -> None:
    workspace = await make_workspace(client, create_session, repos)
    bullet = workspace.claim(DOCKER_BULLET)
    calls_before = dict(fake_provider.calls)

    edited = await workspace.edit(bullet["item_id"], "Containerised services with Docker.", 1)

    changed = workspace.by_id(edited, bullet["item_id"])
    assert changed == {
        "item_id": bullet["item_id"],
        "text": "Containerised services with Docker.",
        "evidence_ids": bullet["evidence_ids"],  # kept for revalidation
        "validation_status": "user_edited",  # the green badge is gone
        "warnings": [],
        "user_edited": True,
    }
    assert edited["revision"] == 2
    assert edited["validation"]["state"] == "needs_revalidation"
    assert edited["validation"]["user_edited_count"] == 1
    assert edited["validation"]["validated_at"] == workspace.body["validation"]["validated_at"]
    # Nothing else changed and nothing was regenerated.
    untouched = [claim for claim in all_claims(edited) if claim["item_id"] != bullet["item_id"]]
    assert untouched == [c for c in all_claims(workspace.body) if c["item_id"] != bullet["item_id"]]
    assert dict(fake_provider.calls) == calls_before
    assert await workspace.get() == edited


async def test_edit_with_a_stale_revision_is_a_conflict(
    client: httpx.AsyncClient, create_session: CreateSession, repos: Repositories
) -> None:
    workspace = await make_workspace(client, create_session, repos)
    bullet = workspace.claim(DOCKER_BULLET)
    await workspace.edit(bullet["item_id"], "First edit with Docker.", 1)

    conflict = await workspace.patch(
        expected_revision=1, edits=[{"item_id": bullet["item_id"], "text": "Second edit."}]
    )

    error = assert_error(conflict, 409, "version_conflict")
    # The client learns which revision to reload.
    assert error["details"] == {"current_revision": 2}
    current = await workspace.get()
    assert workspace.by_id(current, bullet["item_id"])["text"] == "First edit with Docker."
    assert current["revision"] == 2


async def test_edit_of_unknown_item_or_with_invalid_text_is_rejected(
    client: httpx.AsyncClient, create_session: CreateSession, repos: Repositories
) -> None:
    workspace = await make_workspace(client, create_session, repos)
    bullet = workspace.claim(DOCKER_BULLET)

    unknown = await workspace.patch(
        expected_revision=1,
        edits=[
            {"item_id": bullet["item_id"], "text": "Fine."},
            {"item_id": "no-such-item", "text": "Text."},
        ],
        coverage_overrides=[{"requirement_id": "no-such-requirement", "status": "supported"}],
    )
    error = assert_error(unknown, 422, "validation_error")
    assert [item["field"] for item in error["field_errors"]] == [
        "edits.1.item_id",
        "coverage_overrides.0.requirement_id",
    ]
    for payload in (
        {"expected_revision": 1, "edits": [{"item_id": bullet["item_id"], "text": "   "}]},
        {"expected_revision": 1, "edits": [{"item_id": bullet["item_id"], "text": "x" * 1201}]},
        {"edits": []},
        {
            "expected_revision": 1,
            "coverage_overrides": [{"requirement_id": "req-k8s", "status": "great"}],
        },
    ):
        assert_error(await workspace.patch(**payload), 422, "validation_error")
    # A rejected request changes nothing, not even the valid first edit.
    assert await workspace.get() == workspace.body


async def test_saving_unchanged_text_does_not_invalidate_the_item(
    client: httpx.AsyncClient, create_session: CreateSession, repos: Repositories
) -> None:
    workspace = await make_workspace(client, create_session, repos)
    bullet = workspace.claim(DOCKER_BULLET)

    saved = await workspace.edit(bullet["item_id"], DOCKER_BULLET, 1)

    assert workspace.by_id(saved, bullet["item_id"]) == bullet
    assert saved["validation"]["state"] == "validated"
    assert saved["revision"] == 2


# ---- revalidation ------------------------------------------------------------------


async def test_validate_changes_statuses_but_never_text(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    repos: Repositories,
    db: Database,
    fake_provider: FakeProvider,
) -> None:
    workspace = await make_workspace(client, create_session, repos)
    docker = workspace.claim(DOCKER_BULLET)
    costs = workspace.claim(COSTS_BULLET)
    skill = workspace.claim("Redis")
    closing = workspace.body["cover_letter"]["paragraphs"][-1]
    edits = {
        docker["item_id"]: "Containerised services with Docker and Kubernetes.",
        costs["item_id"]: "Reduced cloud costs by 45% by rightsizing instances.",
        skill["item_id"]: "Kubernetes",
        closing["item_id"]: "Thank you for reading. I hope to hear from you.",
    }
    response = await workspace.patch(
        expected_revision=1,
        edits=[{"item_id": item_id, "text": text} for item_id, text in edits.items()],
    )
    assert response.json()["validation"]["user_edited_count"] == 4
    calls_before = dict(fake_provider.calls)

    validated = await workspace.validate()

    assert validated.status_code == 200, validated.text
    body = validated.json()
    result = {item_id: workspace.by_id(body, item_id) for item_id in edits}
    # The text is exactly what the user typed, including the unsupported parts.
    assert {item_id: claim["text"] for item_id, claim in result.items()} == edits
    assert all(claim["user_edited"] for claim in result.values())
    assert result[docker["item_id"]]["validation_status"] == "unsupported"
    assert result[docker["item_id"]]["warnings"] == [
        '"Kubernetes" does not appear anywhere in your confirmed profile.'
    ]
    assert result[costs["item_id"]]["validation_status"] == "unsupported"
    assert result[costs["item_id"]]["warnings"] == ['"45%" does not appear in the cited evidence.']
    assert result[skill["item_id"]]["validation_status"] == "unsupported"
    assert result[skill["item_id"]]["evidence_ids"] == []
    assert result[closing["item_id"]]["validation_status"] == "not_applicable"
    assert body["validation"]["state"] == "validated"
    assert body["validation"]["user_edited_count"] == 0
    assert body["validation"]["unsupported_count"] == 3
    assert body["validation"]["validated_at"] > workspace.body["validation"]["validated_at"]
    assert body["revision"] == 3
    # No text generation happened and the validation quota was used once.
    assert dict(fake_provider.calls) == calls_before
    stored = await db["sessions"].find_one({"_id": workspace.session.session_id})
    assert stored["quota"]["validation"] == 1


async def test_fixing_an_edit_and_validating_again_restores_support(
    client: httpx.AsyncClient, create_session: CreateSession, repos: Repositories
) -> None:
    workspace = await make_workspace(client, create_session, repos)
    docker = workspace.claim(DOCKER_BULLET)
    skill = workspace.claim("Redis")

    await workspace.edit(docker["item_id"], "Packaged services with Docker and Kubernetes.", 1)
    first = (await workspace.validate()).json()
    assert workspace.by_id(first, docker["item_id"])["validation_status"] == "unsupported"

    await workspace.patch(
        expected_revision=first["revision"],
        edits=[
            {"item_id": docker["item_id"], "text": "Containerised twelve services using Docker."},
            {"item_id": skill["item_id"], "text": "PostgreSQL"},
        ],
    )
    second = (await workspace.validate()).json()

    fixed = workspace.by_id(second, docker["item_id"])
    assert (fixed["validation_status"], fixed["warnings"], fixed["user_edited"]) == (
        "supported",
        [],
        True,
    )
    fixed_skill = workspace.by_id(second, skill["item_id"])
    assert fixed_skill["validation_status"] == "supported"
    assert fixed_skill["evidence_ids"]  # the server found where PostgreSQL is mentioned
    assert second["validation"]["unsupported_count"] == 0


async def test_edit_that_borrows_from_elsewhere_in_the_profile_needs_review(
    client: httpx.AsyncClient, create_session: CreateSession, repos: Repositories
) -> None:
    workspace = await make_workspace(client, create_session, repos)
    docker = workspace.claim(DOCKER_BULLET)

    await workspace.edit(docker["item_id"], "Containerised services with Docker and React.", 1)
    body = (await workspace.validate()).json()

    claim = workspace.by_id(body, docker["item_id"])
    assert claim["validation_status"] == "needs_review"
    assert claim["warnings"] == ['"React" is in your profile but not in the evidence cited here.']
    assert body["validation"]["needs_review_count"] == 1


async def test_validate_only_touches_user_edited_items(
    client: httpx.AsyncClient, create_session: CreateSession, repos: Repositories
) -> None:
    workspace = await make_workspace(client, create_session, repos)

    body = (await workspace.validate()).json()

    assert all_claims(body) == all_claims(workspace.body)
    assert body["validation"]["state"] == "validated"
    assert body["revision"] == 2


async def test_stale_draft_is_validated_against_the_evidence_it_was_built_from(
    client: httpx.AsyncClient, create_session: CreateSession, repos: Repositories
) -> None:
    workspace = await make_workspace(client, create_session, repos)
    docker = workspace.claim(DOCKER_BULLET)
    edited_profile = workspace.seeded.profile.model_copy(update={"version": 2, "status": "draft"})
    await repos.profiles.replace(workspace.session.owner_id, edited_profile, expected_version=1)

    await workspace.edit(docker["item_id"], "Containerised services with Docker.", 1)
    body = (await workspace.validate()).json()

    assert body["stale_reasons"] == ["profile_changed"]
    assert workspace.by_id(body, docker["item_id"])["validation_status"] == "supported"


# ---- coverage corrections ----------------------------------------------------------


async def test_coverage_override_is_recorded_as_a_user_correction(
    client: httpx.AsyncClient, create_session: CreateSession, repos: Repositories
) -> None:
    workspace = await make_workspace(client, create_session, repos)
    assert workspace.body["coverage_summary"]["percent"] == 66.7

    response = await workspace.patch(
        expected_revision=1,
        coverage_overrides=[
            {"requirement_id": "req-k8s", "status": "partial", "note": "  Used it in a course.  "},
            {"requirement_id": "req-python", "status": "uncertain"},
        ],
    )

    assert response.status_code == 200, response.text
    body = response.json()
    coverage = {item["requirement_id"]: item for item in body["coverage"]}
    assert (coverage["req-k8s"]["status"], coverage["req-k8s"]["user_corrected"]) == (
        "partial",
        True,
    )
    assert coverage["req-k8s"]["note"] == "Used it in a course."
    assert coverage["req-k8s"]["evidence_ids"] == []  # a correction never invents evidence
    assert (coverage["req-python"]["status"], coverage["req-python"]["note"]) == ("uncertain", None)
    assert coverage["req-docker"]["user_corrected"] is False
    # supported 1, partial 1, uncertain 1 -> 100 * (1 + 0.5) / 2
    assert body["coverage_summary"] == {
        "supported": 1,
        "partial": 1,
        "missing": 0,
        "uncertain": 1,
        "assessed": 2,
        "percent": 75.0,
    }
    # Statements are untouched, so no revalidation is needed.
    assert body["validation"]["state"] == "validated"
    assert all_claims(body) == all_claims(workspace.body)


# ---- single-item regeneration ------------------------------------------------------


async def test_regeneration_requires_an_idempotency_key(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    repos: Repositories,
    fake_provider: FakeProvider,
) -> None:
    workspace = await make_workspace(client, create_session, repos)
    bullet = workspace.claim(DOCKER_BULLET)

    response = await client.post(
        f"{workspace.url}/items/{bullet['item_id']}/regenerate",
        json={},
        headers=workspace.session.headers,
    )

    assert_error(response, 400, "idempotency_key_required")
    assert fake_provider.calls["regenerate_item"] == 0


async def test_regenerated_item_is_validated_and_everything_else_is_kept(
    app: FastAPI,
    client: httpx.AsyncClient,
    create_session: CreateSession,
    repos: Repositories,
    db: Database,
) -> None:
    provider = install_scripted_provider(app)
    workspace = await make_workspace(client, create_session, repos)
    bullet = workspace.claim(DOCKER_BULLET)
    provider.queue_regeneration(
        lambda ctx, target: LLMRegenResult(
            text="Packaged twelve services as Docker containers.",
            evidence=[*target.evidence, "E99"],
            factual=True,
        )
    )

    response = await workspace.regenerate(bullet["item_id"], instruction="  Make it shorter.  ")

    assert response.status_code == 200, response.text
    body = response.json()
    assert workspace.by_id(body, bullet["item_id"]) == {
        "item_id": bullet["item_id"],
        "text": "Packaged twelve services as Docker containers.",
        "evidence_ids": bullet["evidence_ids"],
        "validation_status": "supported",
        "warnings": [],
        "user_edited": False,
    }
    others = [claim for claim in all_claims(body) if claim["item_id"] != bullet["item_id"]]
    assert others == [c for c in all_claims(workspace.body) if c["item_id"] != bullet["item_id"]]
    assert (body["revision"], body["validation"]["state"]) == (2, "validated")
    assert body["usage"]["provider_calls"] == workspace.body["usage"]["provider_calls"] + 1
    assert provider.calls["regenerate_item"] == 1

    # What the model was given: the stored evidence under aliases, the current
    # text, and the instruction as a data field.
    target = provider.regen_targets[0]
    context = provider.contexts[-1]
    assert (target.section, target.current_text, target.instruction) == (
        "experience",
        DOCKER_BULLET,
        "Make it shorter.",
    )
    assert len(target.evidence) == 1 and target.evidence[0].startswith("E")
    assert len(context.evidence) == len(workspace.body["retrieved_evidence_ids"])
    sent = context.model_dump_json() + target.model_dump_json()
    assert not any(evidence_id in sent for evidence_id in workspace.body["retrieved_evidence_ids"])
    stored = await db["sessions"].find_one({"_id": workspace.session.session_id})
    assert stored["quota"]["regeneration"] == 1


async def test_repeating_a_regeneration_key_makes_no_second_provider_call(
    app: FastAPI, client: httpx.AsyncClient, create_session: CreateSession, repos: Repositories
) -> None:
    provider = install_scripted_provider(app)
    workspace = await make_workspace(client, create_session, repos)
    bullet = workspace.claim(DOCKER_BULLET)
    provider.queue_regeneration(
        LLMRegenResult(text="Shipped twelve services in Docker.", evidence=["E1"], factual=True)
    )

    first = await workspace.regenerate(bullet["item_id"], key="regen-same-key")
    second = await workspace.regenerate(bullet["item_id"], key="regen-same-key")

    assert (first.status_code, second.status_code) == (200, 200)
    assert second.json() == first.json()
    assert provider.calls["regenerate_item"] == 1
    # A new key is a new request.
    third = await workspace.regenerate(bullet["item_id"], key="regen-other-key")
    assert third.status_code == 200
    assert provider.calls["regenerate_item"] == 2
    assert third.json()["revision"] == first.json()["revision"] + 1


async def test_regeneration_cannot_be_talked_into_unsupported_content(
    app: FastAPI, client: httpx.AsyncClient, create_session: CreateSession, repos: Repositories
) -> None:
    """The instruction asks for an invented skill and the model complies. The
    result is stored as the user asked, but marked unsupported with the reason."""
    provider = install_scripted_provider(app)
    workspace = await make_workspace(client, create_session, repos)
    bullet = workspace.claim(DOCKER_BULLET)
    provider.queue_regeneration(
        lambda ctx, target: LLMRegenResult(
            text="Kubernetes expert who containerised 40 services.",
            evidence=target.evidence,
            factual=True,
        )
    )

    response = await workspace.regenerate(
        bullet["item_id"], instruction="Say I am a Kubernetes expert with 40 services."
    )

    assert response.status_code == 200
    claim = workspace.by_id(response.json(), bullet["item_id"])
    assert claim["validation_status"] == "unsupported"
    assert '"40" does not appear in the cited evidence.' in claim["warnings"]
    assert '"Kubernetes" does not appear anywhere in your confirmed profile.' in claim["warnings"]
    assert response.json()["validation"]["unsupported_count"] == 1
    assert provider.regen_targets[0].instruction == "Say I am a Kubernetes expert with 40 services."


async def test_regenerated_bullet_may_not_borrow_another_roles_evidence(
    app: FastAPI, client: httpx.AsyncClient, create_session: CreateSession, repos: Repositories
) -> None:
    provider = install_scripted_provider(app)
    workspace = await make_workspace(client, create_session, repos)
    bullet = workspace.claim(DOCKER_BULLET)

    def cite_other_role(ctx: Any, target: Any) -> LLMRegenResult:
        pipelines = next(item.alias for item in ctx.evidence if "data pipelines" in item.text)
        return LLMRegenResult(
            text="Built Python data pipelines for 5,000 daily records.",
            evidence=[pipelines],
            factual=True,
        )

    provider.queue_regeneration(cite_other_role)

    body = (await workspace.regenerate(bullet["item_id"])).json()

    claim = workspace.by_id(body, bullet["item_id"])
    assert (claim["validation_status"], claim["evidence_ids"]) == ("unsupported", [])
    assert (
        claim["warnings"][0]
        == "Evidence from a different role or project was cited and not counted."
    )


async def test_fake_regeneration_stays_within_the_cited_evidence(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    repos: Repositories,
    fake_provider: FakeProvider,
) -> None:
    workspace = await make_workspace(client, create_session, repos)
    letter_body = workspace.body["cover_letter"]["paragraphs"][1]

    response = await workspace.regenerate(letter_body["item_id"])

    assert response.status_code == 200
    claim = workspace.by_id(response.json(), letter_body["item_id"])
    assert claim["text"] != letter_body["text"]
    assert claim["validation_status"] == "supported"
    assert set(claim["evidence_ids"]) <= set(letter_body["evidence_ids"])
    assert fake_provider.calls["regenerate_item"] == 1


async def test_regeneration_input_is_validated(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    repos: Repositories,
    fake_provider: FakeProvider,
) -> None:
    workspace = await make_workspace(client, create_session, repos)
    bullet = workspace.claim(DOCKER_BULLET)
    skill = workspace.claim("Redis")
    degree = entry_for(workspace.body, "education", "edu-fairhaven")["bullets"][0]

    too_long = await workspace.regenerate(bullet["item_id"], instruction="x" * 301)
    unknown = await workspace.regenerate("no-such-item")
    for_skill = await workspace.regenerate(skill["item_id"], key="regen-skill-01")
    for_degree = await workspace.regenerate(degree["item_id"], key="regen-degree-1")
    no_body = await client.post(
        f"{workspace.url}/items/{bullet['item_id']}/regenerate",
        headers=with_key(workspace.session, "regen-no-body"),
    )

    assert_error(too_long, 422, "validation_error")
    assert_error(unknown, 404, "not_found")
    # Skills and education come from the confirmed profile; there is nothing to rewrite.
    assert_error(for_skill, 422, "validation_error")
    assert_error(for_degree, 422, "validation_error")
    assert no_body.status_code == 200  # the body is optional
    assert fake_provider.calls["regenerate_item"] == 1


async def test_failed_regeneration_keeps_the_draft_and_can_be_retried(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    repos: Repositories,
    fake_provider: FakeProvider,
) -> None:
    workspace = await make_workspace(client, create_session, repos)
    bullet = workspace.claim(DOCKER_BULLET)
    fake_provider.fail_next("regenerate_item", ProviderTimeout("The AI provider took too long."))

    failed = await workspace.regenerate(bullet["item_id"], key="regen-retry-1")

    error = assert_error(failed, 504, "provider_timeout")
    assert error["retryable"] is True
    assert await workspace.get() == workspace.body  # completed work is preserved

    retried = await workspace.regenerate(bullet["item_id"], key="regen-retry-1")
    assert retried.status_code == 200
    assert fake_provider.calls["regenerate_item"] == 2  # the key was released, not consumed


async def test_regeneration_quota_is_enforced(
    make_app: Callable[[Settings], Awaitable[FastAPI]],
    make_settings: Callable[..., Settings],
    create_session: CreateSession,
    repos: Repositories,
) -> None:
    limited = await make_app(make_settings(quota_regeneration=1, quota_validation=1))
    async with client_for(limited) as limited_client:
        workspace = await make_workspace(limited_client, create_session, repos)
        bullet = workspace.claim(DOCKER_BULLET)

        first = await workspace.regenerate(bullet["item_id"], key="regen-quota-1")
        second = await workspace.regenerate(bullet["item_id"], key="regen-quota-2")
        replay = await workspace.regenerate(bullet["item_id"], key="regen-quota-1")
        retry = await workspace.regenerate(bullet["item_id"], key="regen-quota-2")
        validated = await workspace.validate()
        validated_again = await workspace.validate()

    assert first.status_code == 200
    assert_error(second, 429, "quota_exceeded")
    assert replay.status_code == 200
    assert_error(retry, 429, "quota_exceeded")  # the refused key was released, not stored
    assert limited.state.provider.calls["regenerate_item"] == 1
    assert validated.status_code == 200
    assert_error(validated_again, 429, "quota_exceeded")


async def test_regeneration_of_a_stale_draft_uses_its_stored_evidence(
    app: FastAPI, client: httpx.AsyncClient, create_session: CreateSession, repos: Repositories
) -> None:
    provider = install_scripted_provider(app)
    workspace = await make_workspace(client, create_session, repos)
    bullet = workspace.claim(DOCKER_BULLET)
    owner_id = workspace.session.owner_id
    # The profile is edited afterwards: version 2 no longer has the Docker bullet.
    edited = workspace.seeded.profile.model_copy(
        update={"version": 2, "status": "draft", "records": []}
    )
    await repos.profiles.replace(owner_id, edited, expected_version=1)

    response = await workspace.regenerate(bullet["item_id"])

    assert response.status_code == 200
    body = response.json()
    assert body["stale_reasons"] == ["profile_changed"]
    context = provider.contexts[-1]
    assert any("Docker for deployment" in item.text for item in context.evidence)
    assert [record.title for record in context.records][:1] == ["Software Engineer"]
    assert workspace.by_id(body, bullet["item_id"])["validation_status"] == "supported"


# ---- isolation between sessions ----------------------------------------------------


async def test_another_session_cannot_reach_a_generation_in_any_way(
    client: httpx.AsyncClient,
    create_session: CreateSession,
    repos: Repositories,
    fake_provider: FakeProvider,
) -> None:
    workspace = await make_workspace(client, create_session, repos)
    bob = await create_session()
    bullet = workspace.claim(DOCKER_BULLET)
    url = workspace.url
    calls_before = dict(fake_provider.calls)

    read = await client.get(url, headers=bob.headers)
    patched = await client.patch(
        url,
        json={"expected_revision": 1, "edits": [{"item_id": bullet["item_id"], "text": "Hacked."}]},
        headers=bob.headers,
    )
    validated = await client.post(f"{url}/validate", headers=bob.headers)
    regenerated = await client.post(
        f"{url}/items/{bullet['item_id']}/regenerate",
        json={},
        headers=with_key(bob, "bob-regen-key"),
    )
    unknown = await client.get("/api/generations/does-not-exist", headers=bob.headers)

    # A foreign ID is indistinguishable from a missing one.
    for response in (read, patched, validated, regenerated):
        error = assert_error(response, 404, "not_found")
        assert error["message"] == assert_error(unknown, 404, "not_found")["message"]
    listed = await client.get("/api/generations", headers=bob.headers)
    assert listed.json() == {"generations": []}
    assert dict(fake_provider.calls) == calls_before
    # Alice's draft is untouched, including its regeneration keys.
    assert await workspace.get() == workspace.body
    stored = await repos.generations.get(
        workspace.session.owner_id, workspace.body["generation_id"]
    )
    assert stored.regeneration_keys == []


async def test_same_idempotency_key_in_two_sessions_gives_two_separate_drafts(
    client: httpx.AsyncClient, create_session: CreateSession, repos: Repositories
) -> None:
    alice = await create_session()
    bob = await create_session()
    await seed_profile(repos, alice)
    await seed_profile(repos, bob)
    alices_job = await seed_job(repos, alice)
    bobs_job = await seed_job(repos, bob)

    first = await post_generation(client, alice, alices_job, key="shared-key-01")
    second = await post_generation(client, bob, bobs_job, key="shared-key-01")

    assert (first.status_code, second.status_code) == (201, 201)
    assert first.json()["generation_id"] != second.json()["generation_id"]
    alice_evidence = {e for claim in all_claims(first.json()) for e in claim["evidence_ids"]}
    bob_evidence = {e for claim in all_claims(second.json()) for e in claim["evidence_ids"]}
    assert alice_evidence and bob_evidence and not alice_evidence & bob_evidence


async def test_deleting_one_session_leaves_the_other_sessions_drafts(
    client: httpx.AsyncClient, create_session: CreateSession, repos: Repositories
) -> None:
    alice = await make_workspace(client, create_session, repos)
    bob = await make_workspace(client, create_session, repos)

    await client.delete("/api/session", headers=alice.session.headers)

    assert await bob.get() == bob.body
    assert await repos.generations.get(alice.session.owner_id, alice.body["generation_id"]) is None

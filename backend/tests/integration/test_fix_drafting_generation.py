"""The review fixes of the drafting rules, through POST /api/generations and
the validate route, with scripted (dishonest or lazy) model output.

Covered: a confirmed role is never printed as a bare heading; a skill the
profile lists under a role is accepted and skills alone never cost a
correction pass; a coursework-only skill keeps its qualifier; a project is not
presented as work at an employer; an edited, uncited paragraph that states a
qualification needs review.
"""

from collections.abc import Awaitable, Callable

import httpx
from fastapi import FastAPI

from app.config import Settings
from app.providers.base import LLMGeneration, LLMGenerationContext, LLMSkillOut
from app.repositories import Repositories
from app.schemas.profiles import ProfileRecord
from app.services.drafting import UNCITED_EDIT_MESSAGE
from tests.conftest import SessionHandle, client_for
from tests.helpers_generation import (
    all_claims,
    entry_for,
    evidence_alias,
    install_scripted_provider,
    llm_entry,
    llm_generation,
    llm_statement,
    post_generation,
    record_alias,
    requirement,
    sample_records,
    sample_requirements,
    seed_job,
    seed_profile,
    with_key,
)

CreateSession = Callable[[], Awaitable[SessionHandle]]

GROUNDED_BULLET = "Containerised services with Docker."
INTERN_BULLET = "Built Python data pipelines for 5,000 daily records."
# Brings the personal project's bullet into the retrieved context.
REACT_REQUIREMENT = requirement("req-react", "React experience", "react")


def one_role_only(ctx: LLMGenerationContext) -> LLMGeneration:
    """A model that writes about the first role and ignores the second."""
    role = record_alias(ctx, "Software Engineer")
    docker = evidence_alias(ctx, "Docker for")
    return llm_generation(experience=[llm_entry(role, (GROUNDED_BULLET, [docker]))])


# ---- G-02: no bare role heading ----------------------------------------------------


async def test_role_the_model_skipped_shows_its_confirmed_bullets_and_can_be_regenerated(
    app: FastAPI, client: httpx.AsyncClient, create_session: CreateSession, repos: Repositories
) -> None:
    provider = install_scripted_provider(app)
    provider.always_generate(one_role_only)
    session = await create_session()
    seeded = await seed_profile(repos, session)
    job = await seed_job(repos, session)

    response = await post_generation(client, session, job)

    assert response.status_code == 201, response.text
    body = response.json()
    intern = entry_for(body, "experience", "role-bright")
    assert [(b["text"], b["validation_status"], b["warnings"]) for b in intern["bullets"]] == [
        (INTERN_BULLET, "supported", [])
    ]
    assert intern["bullets"][0]["evidence_ids"] == [seeded.evidence_id("b-pipelines")]
    # The role the model did write about keeps the model's bullet only.
    engineer = entry_for(body, "experience", "role-quill")
    assert [b["text"] for b in engineer["bullets"]] == [GROUNDED_BULLET]
    # Nothing was wrong with the model's output, so no correction pass and no warning.
    assert provider.calls["generate_documents"] == 1
    assert body["omitted_claims"] == [] and body["warnings"] == []
    assert body["validation"]["needs_review_count"] == 0

    # The citation opens like any other, and the bullet can be regenerated
    # from its evidence.
    evidence = await client.get(
        f"/api/evidence/{intern['bullets'][0]['evidence_ids'][0]}", headers=session.headers
    )
    assert evidence.status_code == 200
    regenerated = await client.post(
        f"/api/generations/{body['generation_id']}/items/{intern['bullets'][0]['item_id']}"
        "/regenerate",
        json={},
        headers=with_key(session, "regen-fallback-1"),
    )
    assert regenerated.status_code == 200, regenerated.text
    target = provider.regen_targets[0]
    assert len(target.evidence) == 1  # the fallback bullet's evidence has an alias


async def test_role_statement_outside_the_retrieved_context_is_still_shown(
    make_app: Callable[[Settings], Awaitable[FastAPI]],
    make_settings: Callable[..., Settings],
    create_session: CreateSession,
    repos: Repositories,
) -> None:
    """With room for a single evidence record the intern role has nothing in
    the model's context, so the model cannot write about it."""
    tight = await make_app(make_settings(retrieval_max_context=1))
    provider = install_scripted_provider(tight)
    session = await create_session()
    seeded = await seed_profile(repos, session)
    job = await seed_job(repos, session)

    async with client_for(tight) as tight_client:
        body = (await post_generation(tight_client, session, job)).json()

    assert len(provider.contexts[0].evidence) == 1
    for record_id in ("role-quill", "role-bright"):
        assert entry_for(body, "experience", record_id)["bullets"], record_id
    intern = entry_for(body, "experience", "role-bright")["bullets"][0]
    assert (intern["text"], intern["validation_status"]) == (INTERN_BULLET, "supported")
    # Stored with the retrieved evidence, after what retrieval selected.
    stored = await repos.generations.get(session.owner_id, body["generation_id"])
    assert seeded.evidence_id("b-pipelines") in stored.retrieved_evidence_ids
    assert len(stored.retrieved_evidence_ids) == len(set(stored.retrieved_evidence_ids))


async def test_project_whose_bullets_were_all_removed_is_left_out(
    app: FastAPI, client: httpx.AsyncClient, create_session: CreateSession, repos: Repositories
) -> None:
    provider = install_scripted_provider(app)

    def invent_for_project(ctx: LLMGenerationContext) -> LLMGeneration:
        project = record_alias(ctx, "TrailNotes")
        trail = evidence_alias(ctx, "hiking journal")
        return llm_generation(
            projects=[llm_entry(project, ("Deployed the app on Kubernetes.", [trail]))]
        )

    provider.always_generate(invent_for_project)
    session = await create_session()
    await seed_profile(repos, session)
    job = await seed_job(repos, session, requirements=[*sample_requirements(), REACT_REQUIREMENT])

    body = (await post_generation(client, session, job)).json()

    assert body["resume"]["projects"] == []
    assert [claim["text"] for claim in body["omitted_claims"]] == [
        "Deployed the app on Kubernetes."
    ]
    assert "Kubernetes" not in str(body["resume"])


# ---- EM-01: skills listed under a role ---------------------------------------------


def records_with_role_skills() -> list[ProfileRecord]:
    records = sample_records()
    records[0].skills = ["Terraform", "Grafana"]
    return records


async def test_skill_listed_under_a_role_is_accepted_without_a_correction_pass(
    app: FastAPI, client: httpx.AsyncClient, create_session: CreateSession, repos: Repositories
) -> None:
    provider = install_scripted_provider(app)

    def lists_skills(ctx: LLMGenerationContext) -> LLMGeneration:
        draft = one_role_only(ctx)
        draft.skills = [
            LLMSkillOut(name="Terraform", evidence=[]),
            LLMSkillOut(name="Docker", evidence=[]),
            LLMSkillOut(name="Kubernetes", evidence=[]),
        ]
        return draft

    provider.always_generate(lists_skills)
    session = await create_session()
    seeded = await seed_profile(repos, session, records=records_with_role_skills())
    job = await seed_job(repos, session)

    body = (await post_generation(client, session, job)).json()

    # The model was offered the role's skills.
    assert {"Terraform", "Grafana"} <= set(provider.contexts[0].profile_skills)
    skills = {skill["text"]: skill for skill in body["resume"]["skills"]}
    assert set(skills) == {"Terraform", "Docker"}
    assert (skills["Terraform"]["validation_status"], skills["Terraform"]["warnings"]) == (
        "supported",
        [],
    )
    assert skills["Terraform"]["evidence_ids"] == [seeded.evidence_id("role-quill")]
    # The invented skill is dropped and reported, but a skill alone does not
    # pay for a second model call.
    assert [(claim["section"], claim["text"]) for claim in body["omitted_claims"]] == [
        ("skills", "Kubernetes")
    ]
    assert provider.calls["generate_documents"] == 1
    assert body["usage"]["provider_calls"] == 2  # embed, generate


async def test_edited_skill_listed_under_a_role_is_supported_on_revalidation(
    app: FastAPI, client: httpx.AsyncClient, create_session: CreateSession, repos: Repositories
) -> None:
    install_scripted_provider(app)
    session = await create_session()
    await seed_profile(repos, session, records=records_with_role_skills())
    job = await seed_job(repos, session)
    body = (await post_generation(client, session, job)).json()
    url = f"/api/generations/{body['generation_id']}"
    skill = body["resume"]["skills"][0]

    patched = await client.patch(
        url,
        json={"expected_revision": 1, "edits": [{"item_id": skill["item_id"], "text": "Grafana"}]},
        headers=session.headers,
    )
    assert patched.status_code == 200, patched.text
    validated = (await client.post(f"{url}/validate", headers=session.headers)).json()

    edited = next(s for s in validated["resume"]["skills"] if s["item_id"] == skill["item_id"])
    assert (edited["text"], edited["validation_status"]) == ("Grafana", "supported")


# ---- G-05: coursework-only skills --------------------------------------------------


async def test_coursework_only_skill_is_not_offered_and_is_flagged_if_the_model_uses_it(
    app: FastAPI, client: httpx.AsyncClient, create_session: CreateSession, repos: Repositories
) -> None:
    provider = install_scripted_provider(app)
    records = sample_records()
    records.append(
        ProfileRecord(
            record_id="skills-coursework",
            category="skill",
            title="Coursework exposure only",
            skills=["TensorFlow"],
            provenance="extracted",
        )
    )

    def promote_coursework(ctx: LLMGenerationContext) -> LLMGeneration:
        coursework = evidence_alias(ctx, "TensorFlow")
        docker = evidence_alias(ctx, "Docker for")
        draft = one_role_only(ctx)
        draft.summary = [
            llm_statement("Engineer experienced with TensorFlow and Docker.", coursework, docker)
        ]
        draft.skills = [LLMSkillOut(name="TensorFlow", evidence=[coursework])]
        return draft

    provider.always_generate(promote_coursework)
    session = await create_session()
    await seed_profile(repos, session, records=records)
    job = await seed_job(
        repos,
        session,
        requirements=[
            *sample_requirements(),
            requirement("req-tensorflow", "TensorFlow experience", "tensorflow"),
        ],
    )

    body = (await post_generation(client, session, job)).json()

    assert "TensorFlow" not in provider.contexts[0].profile_skills
    message = 'Your profile mentions "TensorFlow" only as coursework or limited exposure.'
    skill = body["resume"]["skills"][0]
    assert (skill["text"], skill["validation_status"], skill["warnings"]) == (
        "TensorFlow",
        "needs_review",
        [message],
    )
    summary = body["resume"]["summary"][0]
    assert (summary["validation_status"], summary["warnings"]) == ("needs_review", [message])
    assert body["validation"]["needs_review_count"] == 2
    # Flagged statements are shown for review, not sent back to the model.
    assert provider.calls["generate_documents"] == 1


# ---- G-04: a project presented as work at an employer ------------------------------


async def test_project_presented_as_work_at_an_employer_is_flagged_in_summary_and_letter(
    app: FastAPI, client: httpx.AsyncClient, create_session: CreateSession, repos: Repositories
) -> None:
    provider = install_scripted_provider(app)
    dishonest = "At Brightloom Labs I built a hiking journal app with React and PostgreSQL."
    honest = (
        "At Brightloom Labs I built Python data pipelines for 5,000 daily records. "
        "In a personal project I built a hiking journal app with React and PostgreSQL."
    )

    def merge_records(ctx: LLMGenerationContext) -> LLMGeneration:
        pipelines = evidence_alias(ctx, "data pipelines")
        trail = evidence_alias(ctx, "hiking journal")
        draft = one_role_only(ctx)
        draft.summary = [llm_statement(dishonest, pipelines, trail)]
        draft.cover_letter = [
            llm_statement(dishonest, pipelines, trail),
            llm_statement(honest, pipelines, trail),
        ]
        return draft

    provider.always_generate(merge_records)
    session = await create_session()
    await seed_profile(repos, session)
    job = await seed_job(repos, session, requirements=[*sample_requirements(), REACT_REQUIREMENT])

    body = (await post_generation(client, session, job)).json()

    warning = (
        "This sentence names Brightloom Labs but relies on evidence from another role or project."
    )
    summary = body["resume"]["summary"][0]
    assert (summary["validation_status"], summary["warnings"]) == ("needs_review", [warning])
    merged, separate = body["cover_letter"]["paragraphs"]
    assert (merged["validation_status"], merged["warnings"]) == ("needs_review", [warning])
    assert (separate["validation_status"], separate["warnings"]) == ("supported", [])
    assert body["validation"]["needs_review_count"] == 2


# ---- SPEC-01: an edited paragraph that cites nothing -------------------------------


async def test_edited_uncited_paragraph_that_states_a_qualification_needs_review(
    client: httpx.AsyncClient, create_session: CreateSession, repos: Repositories
) -> None:
    session = await create_session()
    await seed_profile(repos, session)
    job = await seed_job(repos, session)
    body = (await post_generation(client, session, job)).json()
    url = f"/api/generations/{body['generation_id']}"
    greeting, closing = (
        body["cover_letter"]["paragraphs"][0],
        body["cover_letter"]["paragraphs"][-1],
    )
    assert greeting["evidence_ids"] == closing["evidence_ids"] == []
    claim_text = (
        "I led the platform group and shipped the scheduling system used by every customer."
    )

    patched = await client.patch(
        url,
        json={
            "expected_revision": 1,
            "edits": [
                {"item_id": closing["item_id"], "text": claim_text},
                {
                    "item_id": greeting["item_id"],
                    "text": "Dear Hiring Team, I am applying to Globex.",
                },
            ],
        },
        headers=session.headers,
    )
    assert patched.status_code == 200, patched.text
    validated = (await client.post(f"{url}/validate", headers=session.headers)).json()

    by_id = {claim["item_id"]: claim for claim in all_claims(validated)}
    edited = by_id[closing["item_id"]]
    assert (edited["text"], edited["validation_status"], edited["warnings"]) == (
        claim_text,
        "needs_review",
        [UNCITED_EDIT_MESSAGE],
    )
    # A plain greeting is still connective text that needs no citation.
    assert by_id[greeting["item_id"]]["validation_status"] == "not_applicable"
    assert validated["validation"]["state"] == "validated"
    assert validated["validation"]["needs_review_count"] == 1

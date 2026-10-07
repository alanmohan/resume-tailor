"""Building the model's context and composing a draft from its output. Pure
functions: no database, no provider."""

from app.providers.base import LLMCoverageOut, LLMGeneration, LLMJobBrief, LLMSkillOut
from app.schemas.documents import EvidenceDoc, ProfileDoc
from app.services.drafting import (
    OTHER_RECORD_MESSAGE,
    UNKNOWN_RECORD_REASON,
    Draft,
    EvidenceIndex,
    ModelContext,
    briefs_of_evidence,
    briefs_of_profile,
    build_grounding,
    build_model_context,
    check_statement,
    collect_feedback,
    compose_draft,
    profile_skills,
    remove_unsupported,
)
from app.services.generation import stale_reasons
from tests.factories import make_generation, make_job, make_profile
from tests.helpers_generation import (
    CONTACT,
    evidence_alias,
    evidence_for_profile,
    llm_entry,
    llm_generation,
    llm_statement,
    record_alias,
    requirement_alias,
    sample_records,
    sample_requirements,
    seeded_evidence_id,
)

OWNER = "owner-alice"
REQUIREMENTS = sample_requirements()


def sample_profile() -> tuple[ProfileDoc, list[EvidenceDoc]]:
    profile = make_profile(
        OWNER, status="confirmed", index_state="indexed", contact=CONTACT, records=sample_records()
    )
    return profile, evidence_for_profile(profile)


def build(profile: ProfileDoc, evidence: list[EvidenceDoc]) -> tuple[ModelContext, EvidenceIndex]:
    """Context in which every evidence record of the profile was retrieved."""
    index = EvidenceIndex.of(evidence)
    context = build_model_context(
        LLMJobBrief(title="Platform Engineer", company="Globex", role_summary="Run the platform."),
        REQUIREMENTS,
        briefs_of_profile(profile.records),
        evidence,
        {"req-docker": [seeded_evidence_id(profile, "b-docker")], "req-k8s": ["not-retrieved"]},
        index,
        contact_name=profile.contact.name,
        skills=profile_skills(profile.records),
    )
    return context, index


def compose(output: LLMGeneration) -> tuple[Draft, ProfileDoc]:
    profile, evidence = sample_profile()
    context, index = build(profile, evidence)
    grounding = build_grounding(
        index, REQUIREMENTS, job_title="Platform Engineer", company="Globex", contact_name="Jordan"
    )
    return compose_draft(output, profile, context, index, grounding), profile


def aliases() -> ModelContext:
    return build(*sample_profile())[0]


# ---- the model's view --------------------------------------------------------------


def test_model_sees_aliases_and_never_database_ids() -> None:
    profile, evidence = sample_profile()
    context, _ = build(profile, evidence)
    sent = context.llm.model_dump_json()

    for document in evidence:
        assert document.evidence_id not in sent
    for record in profile.records:
        assert record.record_id not in sent
    for requirement in REQUIREMENTS:
        assert requirement.requirement_id not in sent
    assert OWNER not in sent and profile.profile_id not in sent
    assert [item.alias for item in context.llm.evidence] == [
        f"E{n}" for n in range(1, len(evidence) + 1)
    ]
    assert [item.alias for item in context.llm.requirements] == ["R1", "R2", "R3"]


def test_alias_tables_map_back_to_the_ids() -> None:
    profile, evidence = sample_profile()
    context, _ = build(profile, evidence)

    assert list(context.evidence_ids.values()) == [document.evidence_id for document in evidence]
    assert context.requirement_ids == {"R1": "req-python", "R2": "req-docker", "R3": "req-k8s"}
    # Skill groups hold no statements, so they get no record alias.
    assert "skills-main" not in context.record_ids.values()
    assert context.record_ids[record_alias(context.llm, "Software Engineer")] == "role-quill"
    docker_id = seeded_evidence_id(profile, "b-docker")
    assert context.aliases_of([docker_id, "unknown-id"]) == [
        evidence_alias(context.llm, "Docker for")
    ]


def test_context_describes_records_evidence_and_candidates() -> None:
    context = aliases().llm
    role = next(record for record in context.records if record.title == "Software Engineer")
    assert (role.category, role.organization, role.start_date, role.end_date) == (
        "employment",
        "Quillfeather Software",
        "Jul 2022",
        "Jul 2024",
    )
    docker = next(item for item in context.evidence if "Docker for deployment" in item.text)
    # The model gets the original wording and a link to the record, not the
    # indexed text with its "Title at Organisation:" prefix.
    assert docker.text == "Containerised twelve services with Docker for deployment."
    assert docker.record == role.alias
    skills = next(item for item in context.evidence if item.text.startswith("Skills:"))
    assert skills.record is None
    by_text = {requirement.text: requirement for requirement in context.requirements}
    assert by_text["Experience with Docker"].candidate_evidence == [docker.alias]
    assert by_text["Kubernetes experience"].candidate_evidence == []  # unknown IDs are dropped
    assert context.profile_skills == ["Python", "Docker", "PostgreSQL", "React", "Redis"]
    assert context.contact_name == "Jordan Rivera"


def test_header_evidence_is_marked_so_it_never_becomes_a_bullet() -> None:
    records = sample_records()
    records[0].summary = "Owned the deployment tooling of the platform group."
    profile = make_profile(OWNER, status="confirmed", contact=CONTACT, records=records)
    context, _ = build(profile, evidence_for_profile(profile))
    kinds = {item.text: item.kind for item in context.llm.evidence}

    # The record's own header line: proof that the role exists, nothing more.
    header = "Software Engineer - Quillfeather Software (Jul 2022 - Jul 2024)"
    assert kinds[header] == "record_details"
    assert kinds[
        "B.S. in Computer Science - Fairhaven Institute of Technology (Aug 2018 - May 2022)"
    ] == ("record_details")
    # Bullets, the role summary and the skill list are statements.
    assert kinds["Containerised twelve services with Docker for deployment."] == "statement"
    assert kinds["Owned the deployment tooling of the platform group."] == "statement"
    assert kinds["Skills: Python, Docker, PostgreSQL, React, Redis"] == "statement"


def test_record_briefs_can_be_rebuilt_from_stored_evidence() -> None:
    _, evidence = sample_profile()
    briefs = {brief.record_id: brief for brief in briefs_of_evidence(evidence)}
    assert set(briefs) == {record.record_id for record in sample_records()}
    assert (briefs["role-quill"].title, briefs["role-quill"].organization) == (
        "Software Engineer",
        "Quillfeather Software",
    )


# ---- composing ---------------------------------------------------------------------


def test_headers_contact_education_and_certifications_come_from_the_confirmed_profile() -> None:
    draft, profile = compose(llm_generation())
    resume = draft.resume

    assert resume.contact == CONTACT
    # Every confirmed role is listed even though the model wrote nothing.
    assert [
        (e.heading, e.subheading, e.location, e.date_range, e.bullets) for e in resume.experience
    ] == [
        ("Software Engineer", "Quillfeather Software", "Pittsburgh, PA", "Jul 2022 - Jul 2024", []),
        ("Data Engineering Intern", "Brightloom Labs", None, "May 2021 - Aug 2021", []),
    ]
    assert resume.projects == []
    education = resume.education[0]
    assert (education.heading, education.subheading, education.date_range) == (
        "B.S. in Computer Science",
        "Fairhaven Institute of Technology",
        "Aug 2018 - May 2022",
    )
    honours = education.bullets[0]
    assert (honours.text, honours.validation_status) == ("Graduated with honours.", "supported")
    assert honours.evidence_ids == [seeded_evidence_id(profile, "b-honours")]
    certificate = resume.certifications[0]
    assert (certificate.heading, certificate.subheading, certificate.date_range) == (
        "Cloud Practitioner Certificate",
        "Nimbus Training",
        "Mar 2024",
    )


def test_bullets_are_validated_and_filed_under_their_record() -> None:
    ctx = aliases().llm
    role = record_alias(ctx, "Software Engineer")
    docker = evidence_alias(ctx, "Docker for")
    draft, profile = compose(
        llm_generation(
            experience=[llm_entry(role, ("Containerised services with Docker.", [docker]))]
        )
    )
    bullet = draft.resume.experience[0].bullets[0]
    assert bullet.text == "Containerised services with Docker."
    assert bullet.evidence_ids == [seeded_evidence_id(profile, "b-docker")]
    assert (bullet.validation_status, bullet.warnings, bullet.user_edited) == (
        "supported",
        [],
        False,
    )
    assert draft.unsupported_count() == 0


def test_project_listed_as_experience_is_filed_under_projects() -> None:
    ctx = aliases().llm
    project = record_alias(ctx, "TrailNotes")
    trail = evidence_alias(ctx, "hiking journal")
    draft, _ = compose(
        llm_generation(experience=[llm_entry(project, ("Built an app with React.", [trail]))])
    )
    assert all(not role.bullets for role in draft.resume.experience)
    assert [(p.heading, p.subheading, p.category) for p in draft.resume.projects] == [
        ("TrailNotes", "Personal project", "project")
    ]
    assert draft.resume.projects[0].bullets[0].validation_status == "supported"


def test_project_can_be_listed_by_its_confirmed_header_alone() -> None:
    ctx = aliases().llm
    draft, _ = compose(llm_generation(projects=[llm_entry(record_alias(ctx, "TrailNotes"))]))
    assert [(p.heading, p.subheading, p.date_range, p.bullets) for p in draft.resume.projects] == [
        ("TrailNotes", "Personal project", "Jan 2024 - Apr 2024", [])
    ]
    assert draft.unsupported_count() == 0


def test_bullets_under_unknown_or_non_role_records_never_reach_the_document() -> None:
    ctx = aliases().llm
    degree = record_alias(ctx, "B.S. in Computer Science")
    draft, _ = compose(
        llm_generation(
            experience=[llm_entry("P99", ("Chief Architect at Initech, 2010 - 2020.", []))],
            projects=[llm_entry(degree, ("Graduated top of the class.", []))],
        )
    )
    assert [(m.section, m.text, m.reason) for m in draft.misplaced] == [
        ("experience", "Chief Architect at Initech, 2010 - 2020.", UNKNOWN_RECORD_REASON),
        ("projects", "Graduated top of the class.", UNKNOWN_RECORD_REASON),
    ]
    assert draft.unsupported_count() == 2
    headings = [e.heading for e in draft.resume.experience + draft.resume.projects]
    assert headings == ["Software Engineer", "Data Engineering Intern"]


def test_unknown_aliases_and_foreign_ids_are_dropped_and_counted() -> None:
    ctx = aliases().llm
    role = record_alias(ctx, "Software Engineer")
    foreign_id = "0123456789abcdef0123456789abcdef"
    # The real database ID of the Docker evidence (IDs depend on owner and version only).
    real_id = seeded_evidence_id(sample_profile()[0], "b-docker")
    draft, _ = compose(
        llm_generation(
            experience=[
                llm_entry(role, ("Containerised services with Docker.", ["E99", foreign_id]))
            ],
            summary=[llm_statement("Works with Docker.", real_id)],
        )
    )
    bullet = draft.resume.experience[0].bullets[0]
    assert (bullet.evidence_ids, bullet.validation_status) == ([], "unsupported")
    # Even a real evidence ID is refused: the model may only cite aliases.
    assert draft.resume.summary[0].evidence_ids == []
    assert draft.resume.summary[0].validation_status == "unsupported"
    assert draft.ignored_citations == 3


def test_bullet_may_only_cite_evidence_of_its_own_role() -> None:
    ctx = aliases().llm
    role = record_alias(ctx, "Software Engineer")
    trail = evidence_alias(ctx, "hiking journal")
    docker = evidence_alias(ctx, "Docker for")
    draft, _ = compose(
        llm_generation(
            experience=[
                llm_entry(
                    role,
                    ("Built a hiking journal app with React.", [trail]),
                    ("Containerised services with Docker and React.", [docker, trail]),
                )
            ]
        )
    )
    only_project_evidence, mixed = draft.resume.experience[0].bullets
    # A personal project cannot back a bullet under an employer.
    assert only_project_evidence.validation_status == "unsupported"
    assert only_project_evidence.warnings[0] == OTHER_RECORD_MESSAGE
    # With own evidence too, the borrowed part ("React") is flagged for review.
    assert mixed.validation_status == "needs_review"
    assert mixed.warnings == [
        OTHER_RECORD_MESSAGE,
        '"React" is in your profile but not in the evidence cited here.',
    ]


def test_role_title_in_the_indexed_text_does_not_relate_a_claim_to_a_figure() -> None:
    profile, evidence = sample_profile()
    index = EvidenceIndex.of(evidence)
    grounding = build_grounding(
        index, REQUIREMENTS, job_title="Platform Engineer", company="Globex", contact_name="Jordan"
    )
    costs = seeded_evidence_id(profile, "b-costs")
    # The indexed text begins with the role, "[Software Engineer at ...]". That
    # prefix helps retrieval; it is not part of what the 20% measures.
    assert index.documents[costs].text.startswith("[Software Engineer at Quillfeather Software]")

    _, verdict = check_statement(
        "Software engineer who improved API latency by 20%.",
        [costs],
        index,
        grounding,
        section="summary",
    )

    assert verdict.status == "unsupported"
    assert '"20%" appears in the cited evidence, but about something else.' in verdict.warnings
    # The role and employer are still searchable, on lines of their own.
    assert index.searchable[costs].splitlines() == [
        "Reduced cloud costs by 20% by rightsizing instances.",
        "Reduced cloud costs by 20% by rightsizing instances.",
        "Software Engineer",
        "Quillfeather Software",
    ]


def test_skills_need_a_mention_in_the_confirmed_profile() -> None:
    ctx = aliases().llm
    docker = evidence_alias(ctx, "Docker for")
    draft, profile = compose(
        llm_generation(
            skills=[
                LLMSkillOut(name="Docker", evidence=[docker]),
                LLMSkillOut(name="docker", evidence=[]),
                LLMSkillOut(name="Kubernetes", evidence=[docker]),
                LLMSkillOut(name="Redis", evidence=["E99"]),
            ]
        )
    )
    skills = {skill.text: skill for skill in draft.resume.skills}
    assert list(skills) == ["Docker", "Kubernetes", "Redis"]  # duplicate dropped
    assert skills["Docker"].validation_status == "supported"
    assert skills["Docker"].evidence_ids[0] == seeded_evidence_id(profile, "b-docker")
    assert skills["Kubernetes"].validation_status == "unsupported"
    assert skills["Kubernetes"].evidence_ids == []
    assert skills["Kubernetes"].warnings == [
        '"Kubernetes" is not mentioned in your confirmed profile.'
    ]
    # The server finds the citation itself when the model gave a wrong one.
    assert skills["Redis"].validation_status == "supported"
    assert seeded_evidence_id(profile, "skills-main") in skills["Redis"].evidence_ids


def test_cover_letter_paragraphs_follow_the_factual_label() -> None:
    ctx = aliases().llm
    docker = evidence_alias(ctx, "Docker for")
    draft, _ = compose(
        llm_generation(
            cover_letter=[
                llm_statement("Dear Hiring Manager, I am applying to Globex.", factual=False),
                llm_statement("I containerised services with Docker.", docker),
                llm_statement("I also run Kubernetes clusters.", factual=True),
                llm_statement("   ", factual=False),
            ]
        )
    )
    statuses = [paragraph.validation_status for paragraph in draft.cover_letter.paragraphs]
    assert statuses == ["not_applicable", "supported", "unsupported"]


def test_coverage_proposals_are_keyed_by_requirement_with_valid_evidence_only() -> None:
    ctx = aliases().llm
    docker = evidence_alias(ctx, "Docker for")
    draft, profile = compose(
        llm_generation(
            coverage=[
                LLMCoverageOut(
                    requirement=requirement_alias(ctx, "Docker"),
                    status="supported",
                    evidence=[docker, "E99"],
                    rationale="Shown.",
                ),
                LLMCoverageOut(
                    requirement="R99", status="supported", evidence=[docker], rationale="x"
                ),
                LLMCoverageOut(
                    requirement=requirement_alias(ctx, "Docker"),
                    status="missing",
                    evidence=[],
                    rationale="second rating of the same requirement",
                ),
            ]
        )
    )
    assert list(draft.coverage) == ["req-docker"]
    assert draft.coverage["req-docker"].status == "supported"
    assert draft.coverage["req-docker"].evidence_ids == [seeded_evidence_id(profile, "b-docker")]


# ---- feedback and removal ----------------------------------------------------------


def adversarial_draft() -> Draft:
    ctx = aliases().llm
    role = record_alias(ctx, "Software Engineer")
    docker = evidence_alias(ctx, "Docker for")
    trail = evidence_alias(ctx, "hiking journal")
    return compose(
        llm_generation(
            summary=[llm_statement("Works with Kubernetes.", docker)],
            experience=[
                llm_entry(
                    role,
                    ("Containerised services with Docker.", [docker]),
                    ("Ran Kubernetes clusters.", [docker]),
                    ("Expert in Docker.", [docker]),
                ),
                llm_entry("P99", ("Invented role bullet.", [])),
            ],
            projects=[
                llm_entry(record_alias(ctx, "TrailNotes"), ("Deployed on Kubernetes.", [trail]))
            ],
            skills=[LLMSkillOut(name="Kubernetes", evidence=[])],
            cover_letter=[llm_statement("I have 9 years in Kubernetes.", docker)],
        )
    )[0]


def test_feedback_lists_every_flagged_statement_with_its_reason() -> None:
    feedback = collect_feedback(adversarial_draft())
    assert len(feedback) == 7
    assert feedback[0] == (
        'summary: "Works with Kubernetes." - '
        '"Kubernetes" does not appear anywhere in your confirmed profile.'
    )
    assert any(line.startswith('experience: "Expert in Docker."') for line in feedback)
    assert feedback[-1] == f'experience: "Invented role bullet." - {UNKNOWN_RECORD_REASON}'


def test_unsupported_statements_are_removed_and_listed_and_flagged_ones_stay() -> None:
    draft = adversarial_draft()
    omitted = remove_unsupported(draft)

    assert {(item.section, item.text) for item in omitted} == {
        ("experience", "Invented role bullet."),
        ("summary", "Works with Kubernetes."),
        ("experience", "Ran Kubernetes clusters."),
        ("projects", "Deployed on Kubernetes."),
        ("skills", "Kubernetes"),
        ("cover_letter", "I have 9 years in Kubernetes."),
    }
    assert all(item.reason for item in omitted)
    assert draft.resume.summary == []
    kept = [(b.text, b.validation_status) for b in draft.resume.experience[0].bullets]
    assert kept == [
        ("Containerised services with Docker.", "supported"),
        ("Expert in Docker.", "needs_review"),
    ]
    # Entries keep their confirmed headers even when every bullet was removed.
    assert [(p.heading, p.bullets) for p in draft.resume.projects] == [("TrailNotes", [])]
    assert len(draft.resume.experience) == 2
    assert draft.resume.skills == [] and draft.cover_letter.paragraphs == []
    assert "Kubernetes" not in draft.resume.model_dump_json()


# ---- staleness ---------------------------------------------------------------------


def test_draft_is_stale_when_profile_or_job_version_changed() -> None:
    profile = make_profile(OWNER, version=3)
    job = make_job(OWNER, version=2)
    generation = make_generation(
        OWNER, profile_id=profile.profile_id, profile_version=3, job_id=job.job_id, job_version=2
    )
    assert stale_reasons(generation, profile, job) == []

    edited_profile = profile.model_copy(update={"version": 4})
    edited_job = job.model_copy(update={"version": 3})
    assert stale_reasons(generation, edited_profile, job) == ["profile_changed"]
    assert stale_reasons(generation, profile, edited_job) == ["job_changed"]
    assert stale_reasons(generation, edited_profile, edited_job) == [
        "profile_changed",
        "job_changed",
    ]
    assert stale_reasons(generation, None, None) == ["profile_changed", "job_changed"]
    replaced = profile.model_copy(update={"profile_id": "another-profile"})
    assert stale_reasons(generation, replaced, job) == ["profile_changed"]

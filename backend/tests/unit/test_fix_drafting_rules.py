"""Rules added to drafting and retrieval after the review: no bare role
headings, skills the profile lists under a role, coursework-only skills,
sentences that move a project to an employer, courtesy text, repeated bullets,
and the retrieval changes that go with them. Pure functions: no database."""

from typing import Any

import pytest

from app.providers.base import LLMGeneration, LLMJobBrief, LLMSkillOut, Usage
from app.schemas.documents import EvidenceDoc, ProfileDoc
from app.schemas.evidence import EvidenceParent
from app.schemas.generations import Claim
from app.schemas.profiles import ProfileBullet, ProfileRecord
from app.services.drafting import (
    DUPLICATE_BULLET_MESSAGE,
    Draft,
    EvidenceIndex,
    briefs_of_profile,
    build_grounding,
    build_model_context,
    check_skill,
    check_statement,
    compose_draft,
    fill_empty_entries,
    flag_repeated_bullets,
    is_courtesy,
    is_limited_exposure,
    listed_skills,
    profile_skills,
    remove_unsupported,
)
from app.services.retrieval import (
    PythonRetriever,
    RetrievalFilters,
    RetrievalQuery,
    ScoredEvidence,
    best_statement_per_role,
    retrieve_for_job,
    select_context,
)
from app.services.validation import GroundingContext
from tests.factories import make_evidence, make_job, make_profile
from tests.helpers_generation import (
    CONTACT,
    evidence_alias,
    evidence_for_profile,
    llm_entry,
    llm_generation,
    record_alias,
    requirement,
    sample_records,
    sample_requirements,
    seeded_evidence_id,
)

OWNER = "owner-alice"
REQUIREMENTS = sample_requirements()
LIMITED_MESSAGE = 'Your profile mentions "TensorFlow" only as coursework or limited exposure.'


def bullet(bullet_id: str, text: str) -> ProfileBullet:
    return ProfileBullet(bullet_id=bullet_id, text=text, provenance="extracted")


def coursework_group() -> ProfileRecord:
    return ProfileRecord(
        record_id="skills-coursework",
        category="skill",
        title="Coursework exposure only",
        skills=["TensorFlow"],
        provenance="extracted",
    )


class Composed:
    """The sample profile (or ``records``) with every evidence record retrieved."""

    def __init__(self, records: list[ProfileRecord] | None = None) -> None:
        self.profile: ProfileDoc = make_profile(
            OWNER,
            status="confirmed",
            index_state="indexed",
            contact=CONTACT,
            records=sample_records() if records is None else records,
        )
        self.evidence: list[EvidenceDoc] = evidence_for_profile(self.profile)
        self.index = EvidenceIndex.of(self.evidence)
        self.context = build_model_context(
            LLMJobBrief(title="Platform Engineer", company="Globex", role_summary="Run it."),
            REQUIREMENTS,
            briefs_of_profile(self.profile.records),
            self.evidence,
            {},
            self.index,
            contact_name=self.profile.contact.name,
            skills=profile_skills(self.profile.records),
        )
        self.grounding: GroundingContext = build_grounding(
            self.index,
            REQUIREMENTS,
            job_title="Platform Engineer",
            company="Globex",
            contact_name="Jordan Rivera",
        )

    def compose(self, output: LLMGeneration) -> Draft:
        return compose_draft(output, self.profile, self.context, self.index, self.grounding)

    def evidence_id(self, name: str) -> str:
        return seeded_evidence_id(self.profile, name)

    def check(self, text: str, *names: str, section: str = "summary") -> tuple[str, list[str]]:
        _, verdict = check_statement(
            text,
            [self.evidence_id(name) for name in names],
            self.index,
            self.grounding,
            section=section,
        )
        return verdict.status, verdict.warnings


# ---- G-02: no role is printed as a bare heading ------------------------------------


def test_role_without_bullets_gets_its_own_confirmed_bullets_word_for_word() -> None:
    sample = Composed()
    draft = sample.compose(llm_generation())
    remove_unsupported(draft)

    cited = fill_empty_entries(draft, sample.profile, sample.index)

    engineer, intern = draft.resume.experience
    # At most two, in profile order, exactly as confirmed.
    assert [(b.text, b.validation_status, b.warnings) for b in engineer.bullets] == [
        ("Containerised twelve services with Docker for deployment.", "supported", []),
        ("Reduced cloud costs by 20% by rightsizing instances.", "supported", []),
    ]
    assert [b.evidence_ids for b in engineer.bullets] == [
        [sample.evidence_id("b-docker")],
        [sample.evidence_id("b-costs")],
    ]
    assert [b.text for b in intern.bullets] == [
        "Built Python data pipelines for 5,000 daily records."
    ]
    assert cited == [
        sample.evidence_id("b-docker"),
        sample.evidence_id("b-costs"),
        sample.evidence_id("b-pipelines"),
    ]


def test_role_that_kept_a_bullet_is_left_alone() -> None:
    sample = Composed()
    role = record_alias(sample.context.llm, "Software Engineer")
    docker = evidence_alias(sample.context.llm, "Docker for")
    draft = sample.compose(
        llm_generation(
            experience=[llm_entry(role, ("Containerised services with Docker.", [docker]))]
        )
    )
    remove_unsupported(draft)

    fill_empty_entries(draft, sample.profile, sample.index)

    assert [b.text for b in draft.resume.experience[0].bullets] == [
        "Containerised services with Docker."
    ]


def test_role_whose_generated_bullets_were_all_removed_gets_confirmed_ones() -> None:
    sample = Composed()
    role = record_alias(sample.context.llm, "Data Engineering Intern")
    pipelines = evidence_alias(sample.context.llm, "data pipelines")
    draft = sample.compose(
        llm_generation(
            experience=[llm_entry(role, ("Ran Kubernetes clusters for 5,000 pods.", [pipelines]))]
        )
    )
    omitted = remove_unsupported(draft)
    assert [item.text for item in omitted] == ["Ran Kubernetes clusters for 5,000 pods."]

    fill_empty_entries(draft, sample.profile, sample.index)

    assert [b.text for b in draft.resume.experience[1].bullets] == [
        "Built Python data pipelines for 5,000 daily records."
    ]
    assert "Kubernetes" not in draft.resume.model_dump_json()


def test_first_person_remarks_are_not_printed_as_bullets() -> None:
    records = sample_records()
    records[1].bullets = [
        bullet("b-remark", "I did not write down the exact dates of this internship."),
        bullet("b-mine", "My manager asked me to keep the numbers private."),
        bullet("b-etl", "Built nightly ETL jobs in Python."),
    ]
    sample = Composed(records)
    draft = sample.compose(llm_generation())

    fill_empty_entries(draft, sample.profile, sample.index)

    assert [b.text for b in draft.resume.experience[1].bullets] == [
        "Built nightly ETL jobs in Python."
    ]


def test_role_without_bullets_falls_back_to_its_summary_and_else_keeps_its_heading() -> None:
    records = sample_records()
    records[0].bullets = []
    records[0].summary = "Owned the deployment tooling of the platform group."
    records[1].bullets = []
    sample = Composed(records)
    draft = sample.compose(llm_generation())

    fill_empty_entries(draft, sample.profile, sample.index)

    with_summary, heading_only = draft.resume.experience
    assert [b.text for b in with_summary.bullets] == [
        "Owned the deployment tooling of the platform group."
    ]
    assert sample.evidence_id("role-quill-summary") in with_summary.bullets[0].evidence_ids
    # No statement at all in the confirmed record: the role is still history.
    assert (heading_only.heading, heading_only.bullets) == ("Data Engineering Intern", [])


def test_project_without_bullets_is_dropped_unless_its_title_is_all_there_is() -> None:
    records = sample_records()
    records.append(
        ProfileRecord(
            record_id="pub-vectors",
            category="publication",
            title="Notes on Vector Search",
            provenance="extracted",
        )
    )
    sample = Composed(records)
    ctx = sample.context.llm
    draft = sample.compose(
        llm_generation(
            projects=[
                llm_entry(record_alias(ctx, "TrailNotes")),
                llm_entry(record_alias(ctx, "Notes on Vector Search")),
            ]
        )
    )
    assert len(draft.resume.projects) == 2

    fill_empty_entries(draft, sample.profile, sample.index)

    # TrailNotes has a confirmed bullet, so an empty entry would be a bare
    # heading; the publication has nothing but its title.
    assert [(p.heading, p.bullets) for p in draft.resume.projects] == [
        ("Notes on Vector Search", [])
    ]


# ---- EM-01: skills the profile lists under a role or project -----------------------


def records_with_role_skills() -> list[ProfileRecord]:
    records = sample_records()
    records[0].skills = ["Terraform", "AWS (S3, EC2)"]
    return records


def test_skill_listed_under_a_role_is_supported_and_cites_that_role() -> None:
    sample = Composed(records_with_role_skills())
    # The test evidence has no text that mentions Terraform.
    assert not any("terraform" in tokens for tokens in sample.index.tokens.values())

    draft = sample.compose(
        llm_generation(
            skills=[
                LLMSkillOut(name="Terraform", evidence=[]),
                LLMSkillOut(name="aws", evidence=[]),
                LLMSkillOut(name="Kubernetes", evidence=[]),
            ]
        )
    )

    skills = {skill.text: skill for skill in draft.resume.skills}
    assert (skills["Terraform"].validation_status, skills["Terraform"].warnings) == (
        "supported",
        [],
    )
    assert skills["Terraform"].evidence_ids == [sample.evidence_id("role-quill")]
    # "AWS (S3, EC2)" is also known by its name without the note.
    assert skills["aws"].validation_status == "supported"
    assert skills["Kubernetes"].validation_status == "unsupported"
    assert skills["Kubernetes"].warnings == [
        '"Kubernetes" is not mentioned in your confirmed profile.'
    ]


def test_profile_skills_and_listings_cover_every_record() -> None:
    records = records_with_role_skills()
    assert profile_skills(records)[:2] == ["Terraform", "AWS (S3, EC2)"]
    listed = listed_skills(records)
    assert listed["terraform"] == "role-quill"
    assert listed["aws (s3, ec2)"] == listed["aws"] == "role-quill"
    assert listed["python"] == "skills-main"


def test_skills_line_evidence_of_a_role_backs_the_skill() -> None:
    """The indexer may add a "Skills: ..." evidence record per role; then the
    skill is found in the evidence and needs no listing."""
    sample = Composed()
    line = make_evidence(
        OWNER,
        evidence_id="ev-role-skills",
        record_id="role-quill",
        bullet_id=None,
        category="skill",
        excerpt="Skills: Terraform, Ansible",
        text="[Software Engineer at Quillfeather Software] Skills: Terraform, Ansible",
        tags=[],
        parent=EvidenceParent(
            record_id="role-quill",
            category="employment",
            title="Software Engineer",
            organization="Quillfeather Software",
        ),
    )
    index = EvidenceIndex.of([*sample.evidence, line])

    evidence_ids, verdict = check_skill("Terraform", [], index)

    assert (evidence_ids, verdict.status, verdict.warnings) == (
        ["ev-role-skills"],
        "supported",
        [],
    )


def test_only_statements_that_can_be_rewritten_call_for_a_correction_pass() -> None:
    sample = Composed()
    role = record_alias(sample.context.llm, "Software Engineer")
    docker = evidence_alias(sample.context.llm, "Docker for")
    grounded = llm_entry(role, ("Containerised services with Docker.", [docker]))

    only_a_skill = sample.compose(
        llm_generation(experience=[grounded], skills=[LLMSkillOut(name="Kubernetes", evidence=[])])
    )
    assert only_a_skill.unsupported_count() == 1
    assert not only_a_skill.needs_correction_pass()

    bad_bullet = sample.compose(
        llm_generation(experience=[llm_entry(role, ("Ran Kubernetes clusters.", [docker]))])
    )
    assert bad_bullet.needs_correction_pass()
    misplaced = sample.compose(llm_generation(experience=[llm_entry("P99", ("Anything.", []))]))
    assert misplaced.needs_correction_pass()


# ---- G-05: coursework or passing exposure is not an ordinary skill -----------------


@pytest.mark.parametrize(
    "text",
    [
        "Coursework exposure only",
        "TensorFlow (basic knowledge)",
        "Familiar with Rust",
        "Some experience with Go",
        "Introductory course in statistics",
    ],
)
def test_wording_that_marks_limited_exposure(text: str) -> None:
    assert is_limited_exposure(text)


@pytest.mark.parametrize(
    "text",
    ["Frameworks", "Visual Basic", "Acme Limited", "Built a course registration system"],
)
def test_ordinary_wording_is_not_limited_exposure(text: str) -> None:
    assert not is_limited_exposure(text)


def test_coursework_only_skills_are_not_offered_to_the_model() -> None:
    records = [*sample_records(), coursework_group()]
    assert "TensorFlow" not in profile_skills(records)
    assert "tensorflow" not in listed_skills(records)
    assert "Python" in profile_skills(records)


def test_coursework_only_skill_needs_review_unless_it_keeps_its_qualifier() -> None:
    sample = Composed([*sample_records(), coursework_group()])
    listed = listed_skills(sample.profile.records)
    group_evidence = sample.evidence_id("skills-coursework")

    evidence_ids, verdict = check_skill("TensorFlow", [], sample.index, listed)
    assert (verdict.status, verdict.warnings) == ("needs_review", [LIMITED_MESSAGE])
    assert evidence_ids == [group_evidence]

    _, qualified = check_skill("TensorFlow (coursework exposure)", [], sample.index, listed)
    assert (qualified.status, qualified.warnings) == ("supported", [])
    # A skill that is also mentioned without a qualifier is an ordinary skill.
    _, docker = check_skill("Docker", [group_evidence], sample.index, listed)
    assert docker.status == "supported"


def test_skill_used_in_a_bullet_is_supported_even_if_a_coursework_group_lists_it_too() -> None:
    records = [*sample_records(), coursework_group()]
    records[0].bullets.append(bullet("b-tf", "Served TensorFlow models behind a REST API."))
    sample = Composed(records)

    evidence_ids, verdict = check_skill(
        "TensorFlow", [sample.evidence_id("skills-coursework")], sample.index
    )

    assert verdict.status == "supported"
    assert evidence_ids == [sample.evidence_id("b-tf")]


def test_a_role_title_is_not_a_qualifier_of_the_skills_used_in_that_role() -> None:
    records = sample_records()
    records[1].title = "Teaching Assistant, Introductory Programming"
    sample = Composed(records)

    # The bullet of that role is ordinary evidence, whatever the role is called.
    assert sample.evidence_id("b-pipelines") not in sample.index.limited
    records[1].bullets[0].text = "Graded Haskell assignments for 60 students."
    haskell = Composed(records)
    _, verdict = check_skill("Haskell", [], haskell.index)
    assert verdict.status == "supported"


def test_summary_that_drops_the_coursework_qualifier_needs_review() -> None:
    sample = Composed([*sample_records(), coursework_group()])

    status, warnings = sample.check(
        "Experienced with TensorFlow and Docker.", "skills-coursework", "b-docker"
    )
    assert (status, warnings) == ("needs_review", [LIMITED_MESSAGE])

    kept, _ = sample.check(
        "Uses Docker at work and has coursework exposure to TensorFlow.",
        "skills-coursework",
        "b-docker",
    )
    assert kept == "supported"


# ---- G-04: a project is not work done at an employer -------------------------------

BORROWED = (
    "This sentence names Brightloom Labs but relies on evidence from another role or project."
)


@pytest.mark.parametrize("section", ["summary", "cover_letter"])
def test_sentence_that_moves_a_project_to_an_employer_needs_review(section: str) -> None:
    sample = Composed()

    status, warnings = sample.check(
        "At Brightloom Labs I built a hiking journal app with React and PostgreSQL.",
        "b-pipelines",
        "b-trail",
        section=section,
    )

    assert (status, warnings) == ("needs_review", [BORROWED])


def test_sentence_that_moves_another_jobs_figure_to_an_employer_needs_review() -> None:
    sample = Composed()

    status, warnings = sample.check(
        "Built Python data pipelines at Brightloom Labs and reduced cloud costs by 20%.",
        "b-pipelines",
        "b-costs",
    )

    assert (status, warnings) == ("needs_review", [BORROWED])


def test_honest_sentences_about_an_employer_and_a_project_stay_supported() -> None:
    sample = Composed()

    # The employer sentence holds on the employer's evidence alone; the project
    # is described in a sentence of its own.
    two_sentences, _ = sample.check(
        "At Brightloom Labs I built Python data pipelines for 5,000 daily records. "
        "In a personal project I built a hiking journal app with React and PostgreSQL.",
        "b-pipelines",
        "b-trail",
        section="cover_letter",
    )
    assert two_sentences == "supported"
    # One sentence that names both the employer and the project hides nothing.
    both_named, _ = sample.check(
        "Built Python data pipelines at Brightloom Labs and the TrailNotes app with React.",
        "b-pipelines",
        "b-trail",
    )
    assert both_named == "supported"
    # A degree or a skills list is not another role or project.
    with_degree, _ = sample.check(
        "Software Engineer at Quillfeather Software with a B.S. in Computer Science "
        "who works with Docker and Redis.",
        "b-docker",
        "edu-fairhaven",
        "skills-main",
    )
    assert with_degree == "supported"


# ---- SPEC-01: only a plain greeting or closing may go unchecked --------------------


@pytest.mark.parametrize(
    "text",
    [
        "Dear Hiring Manager,",
        "Dear Hiring Manager, I am writing to apply for the Platform Engineer role at Globex.",
        "Thank you for reading. I hope to hear from you.",
        "Thank you for considering my application. I would welcome the chance to discuss "
        "how my background fits this role.",
        "I look forward to hearing from you.\nSincerely,",
    ],
)
def test_greetings_and_closings_are_courtesy_text(text: str) -> None:
    assert is_courtesy(text)


@pytest.mark.parametrize(
    "text",
    [
        "I led the platform group and shipped the scheduling system used by every customer.",
        "I hold a doctorate in computer science and managed large engineering teams.",
        "Dear Hiring Manager, I led the platform group for many years.",
        "Thank you for your time. My background in distributed systems fits this role.",
    ],
)
def test_statements_about_the_applicant_are_not_courtesy_text(text: str) -> None:
    assert not is_courtesy(text)


# ---- EM-07: the same fact twice under one role -------------------------------------


def claim(text: str, status: str = "supported") -> Claim:
    return Claim(item_id=text, text=text, evidence_ids=["e"], validation_status=status)


def test_bullet_that_repeats_an_earlier_bullet_is_flagged_and_the_first_is_kept() -> None:
    bullets = [
        claim("Encrypted customer records with field-level encryption"),
        claim("Reduced cloud costs by 20% by rightsizing instances"),
        claim("Encrypted all customer records using field-level encryption"),
    ]

    flag_repeated_bullets(bullets)

    assert [(b.validation_status, b.warnings) for b in bullets] == [
        ("supported", []),
        ("supported", []),
        ("needs_review", [DUPLICATE_BULLET_MESSAGE]),
    ]


def test_related_but_different_bullets_are_not_flagged() -> None:
    bullets = [
        claim("Built Python data pipelines for 5,000 daily records"),
        claim("Built Python services with FastAPI for the billing team"),
    ]
    flag_repeated_bullets(bullets)
    assert [b.validation_status for b in bullets] == ["supported", "supported"]


def test_repeated_bullets_of_one_role_are_flagged_when_the_draft_is_composed() -> None:
    sample = Composed()
    role = record_alias(sample.context.llm, "Software Engineer")
    docker = evidence_alias(sample.context.llm, "Docker for")
    draft = sample.compose(
        llm_generation(
            experience=[
                llm_entry(
                    role,
                    ("Containerised twelve services with Docker for deployment", [docker]),
                    ("Containerised twelve services with Docker", [docker]),
                )
            ]
        )
    )
    first, second = draft.resume.experience[0].bullets
    assert first.validation_status == "supported"
    assert (second.validation_status, second.warnings) == (
        "needs_review",
        [DUPLICATE_BULLET_MESSAGE],
    )


# ---- Retrieval: plural keywords and one statement per role -------------------------

FILTERS = RetrievalFilters(profile_id="profile-1", embedding_model="tiny-2", embedding_dimension=2)


class StubEvidenceRepository:
    def __init__(self, documents: list[EvidenceDoc]) -> None:
        self.documents = documents

    async def list_for_version(
        self, owner_id: str, profile_id: str, profile_version: int, *, with_vectors: bool
    ) -> list[EvidenceDoc]:
        return self.documents


class StubEmbedder:
    """Embeds every query as the same vector."""

    def __init__(self, vector: list[float]) -> None:
        self.vector = vector

    async def embed(self, texts: list[str]) -> tuple[list[list[float]], Usage]:
        return [self.vector for _ in texts], Usage(provider_calls=1)


def tiny(name: str, vector: list[float], text: str, role: str, **overrides: Any) -> EvidenceDoc:
    values: dict[str, Any] = {
        "evidence_id": name,
        "text": text,
        "tags": [],
        "record_id": role,
        "bullet_id": name,
        "parent": EvidenceParent(record_id=role, category="employment", title=role),
        "embedding": vector,
        "embedding_status": "embedded",
        "embedding_model": "tiny-2",
        "embedding_dimension": 2,
    }
    values.update(overrides)
    return make_evidence(OWNER, **values)


async def test_plural_keyword_matches_a_singular_word_in_the_evidence() -> None:
    documents = [
        tiny("api", [0.0, 0.0], "Built a REST API with Flask", "role-a"),
        tiny("other", [0.0, 0.0], "Wrote onboarding guides", "role-a"),
    ]
    retriever = PythonRetriever(StubEvidenceRepository(documents))
    query = RetrievalQuery(text="q", vector=[1.0, 0.0], keywords=frozenset({"rest", "apis"}))

    ranked = await retriever.retrieve(OWNER, 1, query, FILTERS, 5)

    assert [(c.evidence.evidence_id, c.keyword_score) for c in ranked] == [("api", 1.0)]


def scored(evidence: EvidenceDoc) -> ScoredEvidence:
    return ScoredEvidence(evidence=evidence, similarity=0.5, keyword_score=0.0, fused_score=0.1)


def test_best_statement_per_role_takes_each_roles_best_bullet_only() -> None:
    header = tiny("b-header", [1.0, 0.0], "[role-b] 2021", "role-b", bullet_id=None)
    project = tiny(
        "p1",
        [1.0, 0.0],
        "Built an app",
        "project-p",
        category="project",
        parent=EvidenceParent(record_id="project-p", category="project", title="App"),
    )
    ranked = [
        scored(tiny("a1", [1.0, 0.0], "one", "role-a")),
        scored(header),
        scored(project),
        scored(tiny("a2", [1.0, 0.0], "two", "role-a")),
        scored(tiny("b1", [1.0, 0.0], "three", "role-b")),
    ]
    assert [e.evidence_id for e in best_statement_per_role(ranked)] == ["a1", "b1"]


def test_role_statements_enter_after_every_querys_best_candidate() -> None:
    first = [
        scored(tiny("x1", [1.0, 0.0], "t", "role-a", position=0)),
        scored(tiny("x2", [1.0, 0.0], "t", "role-a", position=1)),
    ]
    second = [
        scored(tiny("y1", [1.0, 0.0], "t", "role-a", position=2)),
        scored(tiny("y2", [1.0, 0.0], "t", "role-a", position=3)),
    ]
    volunteer = tiny("v1", [1.0, 0.0], "t", "role-volunteer", position=9)

    with_room = select_context([first, second], 3, 10_000, [volunteer])
    assert [e.evidence_id for e in with_room] == ["x1", "y1", "v1"]
    # Every query's best evidence comes first, so with no room left the role
    # statement stays out rather than displace it.
    without_room = select_context([first, second], 2, 10_000, [volunteer])
    assert [e.evidence_id for e in without_room] == ["x1", "y1"]
    # Without role statements the third place goes to a second-best candidate
    # (the result is in profile order).
    assert [e.evidence_id for e in select_context([first, second], 3, 10_000)] == [
        "x1",
        "x2",
        "y1",
    ]


async def test_every_role_has_a_statement_in_the_context_when_there_is_room() -> None:
    """Four bullets of one role match the job far better than the volunteer
    role's only bullet, which no query would select on its own."""
    documents = [
        tiny(f"main-{n}", [1.0, 0.0], f"Built Python service number {n}", "role-main", position=n)
        for n in range(4)
    ]
    documents.append(
        tiny("volunteer", [0.2, 1.0], "Rebuilt the events page", "role-volunteer", position=4)
    )
    job = make_job(
        OWNER,
        title="Backend Engineer",
        role_summary="Build Python services.",
        requirements=[requirement("req-python", "Python services", "python")],
    )

    result = await retrieve_for_job(
        PythonRetriever(StubEvidenceRepository(documents)),
        StubEmbedder([1.0, 0.0]),
        OWNER,
        1,
        job,
        FILTERS,
        per_requirement=2,
        max_context=3,
        token_budget=10_000,
    )

    selected = [evidence.evidence_id for evidence in result.evidence]
    assert "volunteer" in selected
    assert len(selected) == 3
    # The requirement still has its best evidence.
    assert result.candidates_by_requirement["req-python"][0] == "main-0"

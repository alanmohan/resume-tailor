"""Coverage compares whole keywords, counts alternatives once, keeps the model's
sentence and never raises a rating (review findings G-01, EM-02, EM-04, EM-09).

The requirement texts are phrases from public job postings; the evidence, the
employers and the people are invented.
"""

import pytest

from app.schemas.jobs import Requirement
from app.services.coverage import (
    MISSING_ADVICE,
    NO_EVIDENCE_RATIONALE,
    NOTHING_TO_CHECK_REASON,
    CoverageProposal,
    assess_requirement,
    build_coverage,
    requirement_groups,
)
from tests.helpers_generation import requirement

PROFILE = {
    "ev-react": "Built React and TypeScript dashboards for internal support agents.",
    "ev-sql": "Cut report time by batching SQL queries.",
    "ev-postgres": "Added PostgreSQL indexes to the orders tables.",
    "ev-actions": "Set up GitHub Actions pipelines that run tests and build Docker images.",
    "ev-pytorch": "Trained image classifiers in Python with PyTorch.",
    "ev-lambda": "Deployed the billing service on AWS Lambda.",
    "ev-hosting": "Moved the billing service to managed cloud hosting.",
    "ev-backend": "Wrote backend services in Go for the orders team.",
    "ev-ml": "Built ML ranking models for the search page.",
    "ev-rest": "Built REST endpoints in Flask for the mobile app.",
    "ev-rag": "Built a retrieval-augmented generation (RAG) service in Python and FastAPI.",
    "ev-eval": "Created an offline evaluation harness for LLM prompt changes.",
    "ev-degree": "B.S. in Electrical Engineering, Fairhaven Institute of Technology.",
    "ev-roles": "Software Engineer at Quillfeather Software. Machine Learning Engineer.",
    "ev-talks": "Presented quarterly results to executives.",
}
SENTENCE = "The cited evidence shows this."
RANK = {"missing": 0, "uncertain": 1, "partial": 2, "supported": 3}


def proposal(status: str, *evidence_ids: str, rationale: str = SENTENCE) -> CoverageProposal:
    return CoverageProposal(status=status, evidence_ids=list(evidence_ids), rationale=rationale)


def labels(text: str, *keywords: str, category: str = "skill") -> list[list[str]]:
    """The requirement's groups of alternatives, as the labels shown to a user."""
    groups = requirement_groups(requirement("req", text, *keywords, category=category), frozenset())
    return [[phrase.label for phrase in group] for group in groups]


# ---- Whole keywords and alternatives -----------------------------------------------


def test_compound_descriptors_are_not_keywords_to_find() -> None:
    assert labels(
        "Front-end experience with React and TypeScript", "front-end", "react", "typescript"
    ) == [["React"], ["TypeScript"]]


def test_such_as_makes_the_category_and_its_example_alternatives() -> None:
    assert labels(
        "Working knowledge of SQL and a relational database such as PostgreSQL",
        "sql",
        "relational database",
        "postgresql",
    ) == [["SQL"], ["relational database", "PostgreSQL"]]
    # With or without "ci/cd" among the keywords, it is one name, not "CI" and "CD".
    for keywords in (["ci/cd", "github actions"], ["github actions"]):
        assert labels("Experience with CI/CD tools such as GitHub Actions", *keywords) == [
            ["CI/CD", "GitHub Actions"]
        ]


def test_bracketed_examples_and_quantifiers_are_any_of() -> None:
    assert labels(
        "Proficiency in Python and familiarity with at least one deep learning framework "
        "(e.g., PyTorch, TensorFlow)",
        "python",
        "pytorch",
        "tensorflow",
    ) == [["Python"], ["PyTorch", "TensorFlow"]]
    assert labels(
        "Solid experience with a major cloud provider (AWS, GCP, Azure)", "aws", "gcp", "azure"
    ) == [["AWS", "GCP", "Azure"]]
    assert labels("At least one of Python, Java and Go", "python", "java", "go") == [
        ["Python", "Java", "Go"]
    ]


def test_or_joins_alternatives_and_and_separates_them() -> None:
    assert labels(
        "Strong Python skills and experience building REST APIs with FastAPI or Flask",
        "python",
        "rest apis",
        "fastapi",
        "flask",
    ) == [["Python"], ["REST APIs"], ["FastAPI", "Flask"]]
    assert labels("Experience with AWS, GCP or Azure", "aws", "gcp", "azure") == [
        ["AWS", "GCP", "Azure"]
    ]
    assert labels("Terraform, Helm, Kafka and Docker", "terraform", "helm", "kafka", "docker") == [
        ["Terraform"],
        ["Helm"],
        ["Kafka"],
        ["Docker"],
    ]
    assert labels(
        "Experience with embeddings, semantic search, or retrieval-augmented generation (RAG)",
        "embeddings",
        "semantic search",
        "retrieval-augmented generation",
        "rag",
    ) == [["embeddings", "semantic search", "retrieval-augmented generation", "RAG"]]
    # A closing bracket ends the list of alternatives.
    assert labels(
        "Experience with container orchestration (Kubernetes) and Docker", "kubernetes", "docker"
    ) == [["Kubernetes"], ["Docker"]]


def test_open_ended_lists_demand_nothing() -> None:
    assert (
        labels(
            "Bachelor's degree in Computer Science or a related field",
            "computer science",
            category="education",
        )
        == []
    )
    assert (
        labels(
            "One or more areas of specialized knowledge (frontend, backend, infrastructure "
            "or other technologies)",
            "frontend",
            "backend",
            "infrastructure",
        )
        == []
    )


def test_keywords_missing_from_the_text_are_alternatives() -> None:
    # They come from the quoted passage; how the posting joined them is unknown.
    assert labels("Experience with a public cloud", "aws", "gcp") == [["aws", "gcp"]]


def test_only_keywords_are_checked_in_a_duty() -> None:
    duty = "Build and improve Atlas, the Northwind knowledge assistant"
    assert labels(duty, category="responsibility") == []
    assert labels(duty, category="skill") == [["Atlas"], ["Northwind"]]


# ---- Honest ratings are kept, with the model's sentence ----------------------------

HONEST = [
    (
        requirement(
            "r1",
            "Front-end experience with React and TypeScript",
            "front-end",
            "react",
            "typescript",
        ),
        ["ev-react"],
    ),
    (
        requirement(
            "r2",
            "Working knowledge of SQL and a relational database such as PostgreSQL",
            "sql",
            "relational database",
            "postgresql",
        ),
        ["ev-sql", "ev-postgres"],
    ),
    (
        requirement(
            "r3", "Experience with CI/CD tools such as GitHub Actions", "ci/cd", "github actions"
        ),
        ["ev-actions"],
    ),
    (
        requirement("r4", "Experience with CI/CD tools such as GitHub Actions", "github actions"),
        ["ev-actions"],
    ),
    (
        requirement(
            "r5",
            "Proficiency in Python and familiarity with at least one deep learning framework "
            "(e.g., PyTorch, TensorFlow)",
            "python",
            "pytorch",
            "tensorflow",
        ),
        ["ev-pytorch"],
    ),
    (
        requirement(
            "r6",
            "Solid experience with a major cloud provider (AWS, GCP, Azure)",
            "aws",
            "gcp",
            "azure",
        ),
        ["ev-lambda"],
    ),
    (
        requirement(
            "r7",
            "One or more areas of specialized knowledge (frontend, backend, infrastructure "
            "or other technologies)",
            "frontend",
            "backend",
            "infrastructure",
        ),
        ["ev-backend"],
    ),
    # "ML" answers "machine learning"; "REST endpoints" answers "REST APIs".
    (
        requirement(
            "r8", "Strong understanding of machine learning fundamentals", "machine learning"
        ),
        ["ev-ml"],
    ),
    (requirement("r9", "Experience building REST APIs", "rest apis"), ["ev-rest"]),
    # "engineering" is answered by the job title "Engineer".
    (
        requirement(
            "r10",
            "2+ years of professional software or machine learning engineering experience",
            "software engineering",
            "machine learning",
            category="experience",
        ),
        ["ev-roles"],
    ),
    (
        requirement(
            "r11",
            "Bachelor's degree in Computer Science or a related field",
            "computer science",
            category="education",
        ),
        ["ev-degree"],
    ),
    (
        requirement(
            "r12",
            "Experience with embeddings, semantic search, or retrieval-augmented generation (RAG)",
            "embeddings",
            "semantic search",
            "retrieval-augmented generation",
            "rag",
        ),
        ["ev-rag"],
    ),
    (
        requirement(
            "r13",
            "Experience building evaluation sets or offline evaluation for machine learning "
            "or LLM features",
            "evaluation sets",
            "offline evaluation",
            "machine learning",
            "llm",
        ),
        ["ev-eval"],
    ),
]


@pytest.mark.parametrize(
    ("asked", "cited"), HONEST, ids=[asked.requirement_id for asked, _ in HONEST]
)
@pytest.mark.parametrize("status", ["supported", "partial"])
def test_honest_rating_and_its_sentence_are_kept(
    asked: Requirement, cited: list[str], status: str
) -> None:
    item = assess_requirement(asked, proposal(status, *cited), PROFILE)
    assert (item.status, item.evidence_ids, item.rationale) == (status, cited, SENTENCE)


# ---- Lowered ratings explain themselves in whole phrases ---------------------------

REST_WITH_FRAMEWORK = requirement(
    "req-rest",
    "Strong Python skills and experience building REST APIs with FastAPI or Flask",
    "python",
    "rest apis",
    "fastapi",
    "flask",
)
CLOUD = requirement(
    "req-cloud",
    "Solid experience with a major cloud provider (AWS, GCP, Azure)",
    "aws",
    "gcp",
    "azure",
)
KUBERNETES = requirement("req-k8s", "Kubernetes experience", "kubernetes")
DOCKER = requirement("req-docker", "Experience with Docker", "docker")


def test_supported_becomes_partial_and_keeps_the_models_sentence() -> None:
    written = "The evidence shows a Python and FastAPI service."
    item = assess_requirement(
        REST_WITH_FRAMEWORK, proposal("supported", "ev-rag", rationale=written), PROFILE
    )
    assert item.status == "partial"
    assert item.rationale == (
        "The evidence shows a Python and FastAPI service. The cited evidence mentions "
        "Python and FastAPI but not REST APIs, so this is rated partial."
    )


def test_unanswered_alternatives_are_named_as_written_in_the_posting() -> None:
    # No provider is named in the cited evidence, but AWS is in the profile:
    # the rating cannot be confirmed, yet the requirement is not "missing".
    written = "The evidence shows a move to managed cloud hosting."
    item = assess_requirement(
        CLOUD, proposal("supported", "ev-hosting", rationale=written), PROFILE
    )
    assert (item.status, item.evidence_ids) == ("uncertain", ["ev-hosting"])
    assert item.rationale == (
        "The evidence shows a move to managed cloud hosting. The cited evidence does not "
        "mention AWS, GCP or Azure, so this rating could not be confirmed."
    )


def test_a_product_of_the_named_cloud_answers_its_abbreviation() -> None:
    # Seen in both real evaluation runs: evidence about a deployment on Google
    # Cloud Run was rated "supported" by the model and lowered because the
    # letters "GCP" are not in it.
    profile = {**PROFILE, "ev-run": "Deployed the proposal tool on Google Cloud Run."}
    written = "The evidence shows applications deployed on Google Cloud Run."
    item = assess_requirement(CLOUD, proposal("supported", "ev-run", rationale=written), profile)
    assert (item.status, item.rationale) == ("supported", written)

    spelled_out = {"ev-aws": "Stored the exports with Amazon Web Services."}
    item = assess_requirement(CLOUD, proposal("supported", "ev-aws"), spelled_out)
    assert item.status == "supported"
    # "Cloud" alone, or Google without its cloud, is still not a provider.
    for text in ("Moved the service to cloud hosting.", "Indexed the pages for Google search."):
        item = assess_requirement(CLOUD, proposal("supported", "ev-x"), {"ev-x": text})
        assert item.status == "uncertain", text


def test_short_and_long_names_of_one_technology_answer_each_other() -> None:
    k8s = requirement("req-k8s", "Experience deploying applications on k8s", "k8s")
    cited = {"ev-k": "Ran the batch jobs on a Kubernetes cluster."}
    assert assess_requirement(k8s, proposal("supported", "ev-k"), cited).status == "supported"
    # Docker is still not Kubernetes under either name.
    docker = {"ev-d": "Packaged services as Docker images."}
    assert assess_requirement(k8s, proposal("supported", "ev-d"), docker).status == "missing"

    postgres = requirement("req-pg", "Experience with Postgres", "postgres")
    assert (
        assess_requirement(postgres, proposal("supported", "ev-postgres"), PROFILE).status
        == "supported"
    )


def test_models_sentence_is_dropped_when_it_names_what_the_evidence_lacks() -> None:
    profile = {**PROFILE, "ev-k8s": "Read the Kubernetes documentation."}
    written = "The applicant has run Kubernetes in production."
    item = assess_requirement(
        KUBERNETES, proposal("supported", "ev-actions", rationale=written), profile
    )
    assert item.status == "uncertain"
    assert item.rationale == (
        "The cited evidence does not mention Kubernetes, so this rating could not be confirmed."
    )


# ---- Deterministic rules that separate a stretch from a fit ------------------------


@pytest.mark.parametrize("status", ["supported", "partial"])
def test_lone_technology_absent_from_the_whole_profile_is_missing(status: str) -> None:
    item = assess_requirement(KUBERNETES, proposal(status, "ev-actions"), PROFILE)
    assert (item.status, item.evidence_ids) == ("missing", [])
    assert item.rationale == (
        f"No evidence of Kubernetes was found in the supplied profile. {MISSING_ADVICE}"
    )


def test_technology_elsewhere_in_the_profile_is_uncertain_not_missing() -> None:
    aws = requirement("req-aws", "Experience with AWS", "aws")
    item = assess_requirement(aws, proposal("supported", "ev-sql"), PROFILE)
    assert item.status == "uncertain"
    assert item.rationale == (
        f"{SENTENCE} The cited evidence does not mention AWS, so this rating could not "
        "be confirmed."
    )


def test_a_duty_is_never_turned_into_missing_by_the_server() -> None:
    duty = requirement(
        "req-duty",
        "Deploy core components of the platform on Kubernetes",
        "kubernetes",
        category="responsibility",
    )
    item = assess_requirement(duty, proposal("partial", "ev-actions"), PROFILE)
    assert item.status == "uncertain"
    assert "does not mention Kubernetes" in item.rationale


@pytest.mark.parametrize("category", ["responsibility", "other"])
def test_partial_for_a_duty_with_nothing_to_check_is_uncertain(category: str) -> None:
    duty = requirement("req-duty", "Work closely with product and design", category=category)
    lowered = assess_requirement(duty, proposal("partial", "ev-talks"), PROFILE)
    assert (lowered.status, lowered.evidence_ids) == ("uncertain", ["ev-talks"])
    assert lowered.rationale == f"{SENTENCE} {NOTHING_TO_CHECK_REASON}"
    # "supported" is the model's judgement and stays; so does a qualification.
    kept = assess_requirement(duty, proposal("supported", "ev-talks"), PROFILE)
    assert (kept.status, kept.rationale) == ("supported", SENTENCE)
    skill = requirement("req-skill", "Comfortable with code review", category="skill")
    assert assess_requirement(skill, proposal("partial", "ev-talks"), PROFILE).status == "partial"


def test_the_hiring_company_is_not_expected_in_the_profile() -> None:
    asked = requirement(
        "req-co", "Experience with Python and the Northwind platform", "python", "northwind"
    )
    proposals = {"req-co": proposal("supported", "ev-pytorch")}
    lowered = build_coverage([asked], proposals, PROFILE)[0]
    assert lowered.status == "partial"
    kept = build_coverage([asked], proposals, PROFILE, company="Northwind Labs")[0]
    assert (kept.status, kept.rationale) == ("supported", SENTENCE)


def test_rating_without_valid_evidence_is_still_uncertain() -> None:
    for status in ("supported", "partial"):
        item = assess_requirement(DOCKER, proposal(status), PROFILE)
        assert (item.status, item.rationale) == ("uncertain", NO_EVIDENCE_RATIONALE)


ALL_REQUIREMENTS = [asked for asked, _ in HONEST] + [
    REST_WITH_FRAMEWORK,
    CLOUD,
    KUBERNETES,
    DOCKER,
    requirement("req-duty", "Work closely with product and design", category="other"),
]


@pytest.mark.parametrize("status", ["supported", "partial", "uncertain", "missing"])
@pytest.mark.parametrize("cited", [[], ["ev-talks"], ["ev-actions", "ev-pytorch"], list(PROFILE)])
def test_the_server_never_raises_a_rating(status: str, cited: list[str]) -> None:
    proposals = {asked.requirement_id: proposal(status, *cited) for asked in ALL_REQUIREMENTS}
    for item in build_coverage(ALL_REQUIREMENTS, proposals, PROFILE):
        assert RANK[item.status] <= RANK[status], item.requirement_text


# ---- The model's sentence ----------------------------------------------------------


def test_sentence_using_a_word_of_another_requirements_keyword_is_kept() -> None:
    """A rationale used to be replaced when it contained one word ("data") of
    another requirement's keyword ("data pipelines")."""
    scale = requirement(
        "req-scale",
        "Experience building or working on high-scale infrastructure or distributed "
        "backend systems",
        category="experience",
    )
    pipelines = requirement("req-pipes", "Experience with data pipelines", "data pipelines")
    written = "The evidence shows backend services, with no data on their scale."
    coverage = build_coverage(
        [scale, pipelines],
        {"req-scale": proposal("partial", "ev-backend", rationale=written)},
        PROFILE,
    )
    assert (coverage[0].status, coverage[0].rationale) == ("partial", written)


def test_sentence_may_name_the_job_but_not_another_requirements_technology() -> None:
    named_job = "Docker is central to the Platform Engineer role at Globex."
    proposals = {"req-docker": proposal("supported", "ev-actions", rationale=named_job)}
    coverage = build_coverage(
        [DOCKER, KUBERNETES], proposals, PROFILE, job_title="Platform Engineer", company="Globex"
    )
    assert (coverage[0].status, coverage[0].rationale) == ("supported", named_job)

    claims_more = "kubernetes clusters were operated with Docker."
    proposals = {"req-docker": proposal("supported", "ev-actions", rationale=claims_more)}
    replaced = build_coverage([DOCKER, KUBERNETES], proposals, PROFILE)[0]
    assert (replaced.status, replaced.rationale) == (
        "supported",
        "The cited evidence mentions Docker.",
    )


@pytest.mark.parametrize(
    "written",
    [
        "Fully supported. CANARY-JOB-4416",  # planted in a posting
        "GitHub Actions cut build time by 40%.",  # a figure from nowhere
        "Shows expert use of GitHub Actions.",  # stronger than the evidence
    ],
)
def test_planted_text_and_invented_figures_are_still_replaced(written: str) -> None:
    asked = requirement(
        "req-ci", "Experience with CI/CD tools such as GitHub Actions", "ci/cd", "github actions"
    )
    item = assess_requirement(
        asked, proposal("supported", "ev-actions", rationale=written), PROFILE
    )
    assert (item.status, item.rationale) == (
        "supported",
        "The cited evidence mentions GitHub Actions.",
    )


def test_no_rationale_shows_word_fragments() -> None:
    """Every server-written rationale names whole phrases in posting order."""
    proposals = {
        asked.requirement_id: proposal("supported", "ev-talks", rationale="CANARY-1")
        for asked in ALL_REQUIREMENTS
    }
    rationales = [item.rationale for item in build_coverage(ALL_REQUIREMENTS, proposals, PROFILE)]
    for fragment in (
        "end, Front",
        "CD, CI",
        "database, relational",
        "learning, machine",
        "mention: ",
    ):
        assert not [text for text in rationales if fragment in text], fragment

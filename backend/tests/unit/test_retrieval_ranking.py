"""Ranking maths and context selection of the retrieval service. No database:
the retriever reads from a stub repository."""

from typing import Any

import numpy as np
import pytest

from app.errors import ProfileNotIndexed
from app.schemas.documents import EvidenceDoc
from app.schemas.evidence import EvidenceParent
from app.services.retrieval import (
    RRF_K,
    PythonRetriever,
    RetrievalFilters,
    RetrievalQuery,
    ScoredEvidence,
    cosine_similarities,
    diversify_by_parent,
    reciprocal_rank_fusion,
    role_query_text,
    select_context,
)
from tests.factories import make_evidence, make_job

OWNER = "owner-alice"
FILTERS = RetrievalFilters(profile_id="profile-1", embedding_model="tiny-2", embedding_dimension=2)


# ---- cosine similarity -------------------------------------------------------------


def test_cosine_of_parallel_orthogonal_and_opposite_vectors() -> None:
    matrix = np.array([[2.0, 0.0], [0.0, 3.0], [-1.0, 0.0], [1.0, 1.0]])
    similarities = cosine_similarities(np.array([1.0, 0.0]), matrix)
    assert similarities == pytest.approx([1.0, 0.0, -1.0, 1 / np.sqrt(2)])


def test_cosine_ignores_vector_length() -> None:
    matrix = np.array([[0.3, 0.4]])
    short = cosine_similarities(np.array([3.0, 4.0]), matrix)
    long = cosine_similarities(np.array([300.0, 400.0]), matrix)
    assert short == pytest.approx([1.0])
    assert long == pytest.approx([1.0])


def test_cosine_of_a_zero_row_is_zero_not_nan() -> None:
    matrix = np.array([[0.0, 0.0], [1.0, 0.0]])
    similarities = cosine_similarities(np.array([1.0, 0.0]), matrix)
    assert similarities.tolist() == [0.0, 1.0]
    assert not np.isnan(similarities).any()


def test_cosine_with_a_zero_query_is_zero_for_every_row() -> None:
    matrix = np.array([[1.0, 0.0], [0.0, 1.0], [0.0, 0.0]])
    assert cosine_similarities(np.zeros(2), matrix).tolist() == [0.0, 0.0, 0.0]


def test_cosine_of_an_empty_matrix_is_empty() -> None:
    assert cosine_similarities(np.array([1.0, 0.0]), np.zeros((0, 2))).tolist() == []


# ---- reciprocal rank fusion --------------------------------------------------------


def test_rrf_score_is_the_sum_of_reciprocal_ranks() -> None:
    scores = reciprocal_rank_fusion([[7, 8, 9], [9, 7]])
    assert scores[7] == pytest.approx(1 / (RRF_K + 1) + 1 / (RRF_K + 2))
    assert scores[9] == pytest.approx(1 / (RRF_K + 3) + 1 / (RRF_K + 1))
    assert scores[8] == pytest.approx(1 / (RRF_K + 2))  # only in the first ranking


def test_rrf_orders_items_by_summed_reciprocal_ranks() -> None:
    semantic = [0, 1, 2, 3]
    keyword = [2, 1]
    scores = reciprocal_rank_fusion([semantic, keyword])
    ordered = sorted(scores, key=lambda item: -scores[item])
    # 1 is second in both lists, 2 is third and first: almost a tie, 1/62+1/62
    # against 1/63+1/61, which the first-place rank narrowly wins.
    assert ordered == [2, 1, 0, 3]


def test_rrf_of_no_rankings_is_empty() -> None:
    assert reciprocal_rank_fusion([]) == {}
    assert reciprocal_rank_fusion([[], []]) == {}


# ---- PythonRetriever ---------------------------------------------------------------


class StubEvidenceRepository:
    """Returns fixed documents and records how it was queried."""

    def __init__(self, documents: list[EvidenceDoc]) -> None:
        self.documents = documents
        self.calls: list[tuple[Any, ...]] = []

    async def list_for_version(
        self, owner_id: str, profile_id: str, profile_version: int, *, with_vectors: bool
    ) -> list[EvidenceDoc]:
        self.calls.append((owner_id, profile_id, profile_version, with_vectors))
        return self.documents


def tiny_evidence(name: str, vector: list[float], text: str, **overrides: Any) -> EvidenceDoc:
    values: dict[str, Any] = {
        "evidence_id": name,
        "text": text,
        "tags": [],
        "embedding": vector,
        "embedding_status": "embedded",
        "embedding_model": "tiny-2",
        "embedding_dimension": 2,
    }
    values.update(overrides)
    return make_evidence(OWNER, **values)


def query(vector: list[float], *keywords: str) -> RetrievalQuery:
    return RetrievalQuery(text="query", vector=vector, keywords=frozenset(keywords))


async def retrieve(
    documents: list[EvidenceDoc], retrieval_query: RetrievalQuery, limit: int = 10
) -> list[ScoredEvidence]:
    retriever = PythonRetriever(StubEvidenceRepository(documents))
    return await retriever.retrieve(OWNER, 1, retrieval_query, FILTERS, limit)


async def test_hybrid_ranking_fuses_semantic_and_keyword_ranks() -> None:
    documents = [
        tiny_evidence("semantic-only", [1.0, 0.0], "alpha beta"),
        tiny_evidence("keyword-only", [0.0, 1.0], "kubernetes operator"),
        tiny_evidence("both", [0.9, 0.436], "kubernetes cluster"),
        tiny_evidence("neither", [0.0, 1.0], "gamma delta"),
    ]
    ranked = await retrieve(documents, query([1.0, 0.0], "kubernetes", "operator"))

    names = [candidate.evidence.evidence_id for candidate in ranked]
    # "both" is second by similarity and second by keywords: 2 * 1/62.
    # "semantic-only" and "keyword-only" each lead one list: 1/61 each.
    assert names == ["both", "semantic-only", "keyword-only"]
    assert "neither" not in names  # similarity 0 and no keyword: no signal at all
    assert ranked[0].fused_score == pytest.approx(2 / (RRF_K + 2))
    assert ranked[1].fused_score == pytest.approx(1 / (RRF_K + 1))


async def test_ties_keep_profile_order_and_limit_is_applied() -> None:
    documents = [tiny_evidence(f"doc-{n}", [1.0, 0.0], "same words") for n in range(5)]
    ranked = await retrieve(documents, query([1.0, 0.0], "same"), limit=3)
    assert [candidate.evidence.evidence_id for candidate in ranked] == ["doc-0", "doc-1", "doc-2"]


async def test_zero_vectors_are_ranked_by_keywords_only() -> None:
    documents = [
        tiny_evidence("zero-with-keyword", [0.0, 0.0], "docker images"),
        tiny_evidence("zero-without", [0.0, 0.0], "unrelated"),
    ]
    ranked = await retrieve(documents, query([1.0, 0.0], "docker"))
    assert [candidate.evidence.evidence_id for candidate in ranked] == ["zero-with-keyword"]
    assert ranked[0].similarity == 0.0
    assert ranked[0].keyword_score == 1.0


async def test_keywords_also_match_evidence_tags() -> None:
    documents = [tiny_evidence("tagged", [0.0, 1.0], "built services", tags=["PostgreSQL"])]
    ranked = await retrieve(documents, query([1.0, 0.0], "postgresql"))
    assert [candidate.evidence.evidence_id for candidate in ranked] == ["tagged"]


async def test_returned_evidence_carries_no_vector() -> None:
    ranked = await retrieve([tiny_evidence("doc", [1.0, 0.0], "text")], query([1.0, 0.0]))
    assert ranked[0].evidence.embedding is None


async def test_no_evidence_gives_an_empty_result() -> None:
    assert await retrieve([], query([1.0, 0.0], "python")) == []


@pytest.mark.parametrize(
    "broken",
    [
        {"embedding_model": "another-model"},
        {"embedding_dimension": 3, "embedding": [1.0, 0.0, 0.0]},
        {"embedding": [1.0, 0.0, 0.0]},
        {"embedding_status": "pending", "embedding": None},
        {"embedding_status": "failed"},
    ],
)
async def test_mixed_or_incomplete_embeddings_are_refused(broken: dict[str, Any]) -> None:
    documents = [
        tiny_evidence("good", [1.0, 0.0], "python"),
        tiny_evidence("bad", [1.0, 0.0], "python", **broken),
    ]
    with pytest.raises(ProfileNotIndexed):
        await retrieve(documents, query([1.0, 0.0], "python"))


async def test_query_vector_of_another_dimension_is_a_programming_error() -> None:
    with pytest.raises(ValueError):
        await retrieve([tiny_evidence("doc", [1.0, 0.0], "text")], query([1.0, 0.0, 0.0]))


async def test_evidence_is_loaded_once_per_request_with_owner_profile_and_version() -> None:
    repository = StubEvidenceRepository([tiny_evidence("doc", [1.0, 0.0], "python")])
    retriever = PythonRetriever(repository)
    for _ in range(3):
        await retriever.retrieve(OWNER, 4, query([1.0, 0.0], "python"), FILTERS, 5)
    assert repository.calls == [(OWNER, "profile-1", 4, True)]


# ---- diversity and context selection -----------------------------------------------


def scored(name: str, parent: str, *, position: int = 0, text: str = "short") -> ScoredEvidence:
    evidence = make_evidence(
        OWNER,
        evidence_id=name,
        position=position,
        text=text,
        parent=EvidenceParent(record_id=parent, category="employment", title=parent),
    )
    return ScoredEvidence(evidence=evidence, similarity=0.5, keyword_score=0.0, fused_score=0.1)


def names(candidates: list[ScoredEvidence]) -> list[str]:
    return [candidate.evidence.evidence_id for candidate in candidates]


def test_diversity_prefers_other_roles_over_a_third_record_of_the_same_role() -> None:
    ranked = [
        scored("a1", "role-a"),
        scored("a2", "role-a"),
        scored("a3", "role-a"),
        scored("a4", "role-a"),
        scored("b1", "role-b"),
        scored("c1", "project-c"),
    ]
    assert names(diversify_by_parent(ranked, limit=4)) == ["a1", "a2", "b1", "c1"]


def test_diversity_fills_remaining_places_from_the_same_role() -> None:
    ranked = [scored("a1", "role-a"), scored("a2", "role-a"), scored("a3", "role-a")]
    ranked.append(scored("b1", "role-b"))
    # Only two roles exist, so the fourth place goes back to role-a, in rank order.
    assert names(diversify_by_parent(ranked, limit=4)) == ["a1", "a2", "a3", "b1"]
    assert names(diversify_by_parent(ranked, limit=2)) == ["a1", "a2"]
    assert diversify_by_parent([], limit=4) == []


def test_context_takes_every_querys_best_candidate_before_any_second_one() -> None:
    first_query = [scored("x1", "r1", position=5), scored("x2", "r1", position=6)]
    second_query = [scored("y1", "r2", position=1), scored("y2", "r2", position=2)]
    selected = select_context([first_query, second_query], max_records=3, token_budget=10_000)
    # x1 and y1 first, then x2; y2 no longer fits. Result is in profile order.
    assert [evidence.evidence_id for evidence in selected] == ["y1", "x1", "x2"]


def test_context_deduplicates_evidence_retrieved_for_several_queries() -> None:
    shared = scored("shared", "r1")
    selected = select_context(
        [[shared, scored("x2", "r1", position=1)], [shared, scored("y2", "r2", position=2)]],
        max_records=10,
        token_budget=10_000,
    )
    assert [evidence.evidence_id for evidence in selected] == ["shared", "x2", "y2"]


def test_context_respects_the_token_budget_and_skips_only_what_does_not_fit() -> None:
    long_text = "word " * 400  # about 500 estimated tokens
    candidates = [
        scored("small-1", "r1", position=0, text="tiny"),
        scored("huge", "r1", position=1, text=long_text),
        scored("small-2", "r2", position=2, text="tiny"),
    ]
    selected = select_context([candidates], max_records=10, token_budget=50)
    assert [evidence.evidence_id for evidence in selected] == ["small-1", "small-2"]


def test_context_of_no_candidates_is_empty() -> None:
    assert select_context([], max_records=5, token_budget=100) == []
    assert select_context([[], []], max_records=5, token_budget=100) == []


# ---- role query --------------------------------------------------------------------


def test_role_query_uses_title_and_summary() -> None:
    job = make_job(OWNER, title="Platform Engineer", role_summary="Run the container platform.")
    assert role_query_text(job) == "Platform Engineer. Run the container platform."


def test_role_query_falls_back_to_the_start_of_the_posting() -> None:
    job = make_job(OWNER, title=None, role_summary=None, description="We  need\nPython. " * 200)
    text = role_query_text(job)
    assert text.startswith("We need Python.")
    assert len(text) == 600

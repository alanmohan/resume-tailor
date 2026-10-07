"""Evidence retrieval: choosing the profile evidence that grounds one generation.

Pipeline (the "R" of this application's retrieval-augmented generation):

1. One query per job requirement plus one query for the role as a whole. All
   query texts are embedded in a single batched provider call.
2. A Retriever ranks the evidence of exactly one owner and one profile version
   for each query. The only implementation, PythonRetriever, loads that
   version's evidence with a database query that filters on owner, profile and
   version, then ranks it in memory with numpy. Evidence of another visitor or
   another profile version is never loaded, so it cannot be returned.
3. Each query keeps a few candidates, spread over different roles/projects.
4. The candidates of all queries are merged into one bounded context: no
   duplicates, at most RETRIEVAL_MAX_CONTEXT records and RETRIEVAL_TOKEN_BUDGET
   estimated tokens. Once every query has its best candidate in, each
   confirmed role gets one statement of its own while there is room, so the
   model can write about every role.

Ranking scores are similarity measures used for ordering only. They never
leave this module and are not confidence values: whether a requirement is
actually supported is decided later by the validators and the coverage rules.

MongoDB Atlas Vector Search could replace PythonRetriever behind the same
Retriever protocol; this application does not use it.
"""

from dataclasses import dataclass
from typing import Protocol

import numpy as np

from app.errors import ProfileNotIndexed
from app.providers.base import AIProvider, Usage
from app.repositories.evidence import EvidenceRepository
from app.schemas.documents import EvidenceDoc, JobDoc
from app.services.textutil import (
    estimate_tokens,
    keyword_overlap,
    strip_contact_details,
    tokenize,
)
from app.services.validation import singular_tokens

# Constant of reciprocal rank fusion. 60 is the value from the original RRF
# paper (Cormack et al., 2009); it keeps one top rank from dominating the sum.
RRF_K = 60
# How many more candidates than needed are ranked per query, so that the
# diversity rule has alternatives to choose from.
CANDIDATE_POOL_FACTOR = 3
# Preferred maximum number of candidates per query from one role or project.
MAX_PER_PARENT = 2
ROLE_QUERY_MAX_CHARS = 600
EMPLOYMENT_CATEGORY = "employment"
# Limit used to rank a whole profile for the role query. Far above
# MAX_EVIDENCE_CHUNKS (200 by default), so no evidence record is cut off.
WHOLE_PROFILE = 10_000


@dataclass(frozen=True)
class RetrievalQuery:
    """One thing to find evidence for: the text, its embedding and its keywords."""

    text: str
    vector: list[float]
    keywords: frozenset[str]


@dataclass(frozen=True)
class RetrievalFilters:
    """Restrictions that are part of the database query (profile) or that the
    loaded evidence must satisfy (embedding model and dimension)."""

    profile_id: str
    embedding_model: str
    embedding_dimension: int


@dataclass(frozen=True)
class ScoredEvidence:
    """A ranked candidate. The scores are internal ordering aids."""

    evidence: EvidenceDoc  # without its vector
    similarity: float
    keyword_score: float
    fused_score: float


@dataclass(frozen=True)
class RetrievalResult:
    """What was retrieved for one job, kept with the draft for auditability."""

    profile_version: int
    # The selected context in profile order, without vectors.
    evidence: list[EvidenceDoc]
    # requirement_id -> IDs of the selected evidence retrieved for it, best first.
    candidates_by_requirement: dict[str, list[str]]
    usage: Usage


class Retriever(Protocol):
    async def retrieve(
        self,
        owner_id: str,
        profile_version: int,
        query: RetrievalQuery,
        filters: RetrievalFilters,
        limit: int,
    ) -> list[ScoredEvidence]:
        """The best ``limit`` evidence records of this owner and profile
        version for ``query``, best first."""
        ...


# ---- Ranking maths -----------------------------------------------------------------


def cosine_similarities(query: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    """Cosine similarity between ``query`` (shape d) and every row of
    ``matrix`` (shape n x d), clipped to [-1, 1].

    cos(q, r) = (q . r) / (|q| * |r|). A zero vector has no direction, so the
    division would be 0/0; its similarity is defined as 0 here ("no signal").
    The fake provider produces zero vectors for text without words.
    """
    if matrix.shape[0] == 0:
        return np.zeros(0)
    dot_products = matrix @ query
    norms = np.linalg.norm(matrix, axis=1) * np.linalg.norm(query)
    similarities = np.divide(
        dot_products, norms, out=np.zeros_like(dot_products, dtype=float), where=norms > 0
    )
    return np.clip(similarities, -1.0, 1.0)


def reciprocal_rank_fusion(rankings: list[list[int]], k: int = RRF_K) -> dict[int, float]:
    """Combine several rankings of the same items into one score per item.

    Each ranking lists item indexes, best first. An item at rank r (1 = best)
    in a ranking receives 1 / (k + r) from it; the fused score is the sum over
    all rankings that contain the item:

        score(item) = sum over rankings of 1 / (k + rank_in_that_ranking)

    Only ranks are used, never raw scores, so a cosine similarity and a keyword
    overlap can be combined although they are on different scales. An item
    missing from a ranking simply gets nothing from it.
    """
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return scores


def _ranking(scores: list[float]) -> list[int]:
    """Indexes with a positive score, best first; ties keep profile order.
    Items scoring 0 or less carry no signal and are left out."""
    positive = [index for index, score in enumerate(scores) if score > 0]
    return sorted(positive, key=lambda index: (-scores[index], index))


# ---- The Python retriever ----------------------------------------------------------


@dataclass(frozen=True)
class _LoadedEvidence:
    """One profile version's evidence prepared for ranking."""

    documents: list[EvidenceDoc]  # vectors removed
    matrix: np.ndarray  # one row per document
    # Words of text and tags in singular form, for keyword overlap.
    token_sets: list[frozenset[str]]


class PythonRetriever:
    """Ranks one profile version's evidence in memory.

    The profile is bounded (MAX_EVIDENCE_CHUNKS, 200 by default), so an exact
    scan over every vector is cheap and no vector index is needed. Create one
    instance per request: it loads a version's evidence once and reuses it for
    every query of that request.
    """

    def __init__(self, evidence: EvidenceRepository) -> None:
        self._evidence = evidence
        self._loaded: dict[tuple[str, str, int], _LoadedEvidence] = {}

    async def retrieve(
        self,
        owner_id: str,
        profile_version: int,
        query: RetrievalQuery,
        filters: RetrievalFilters,
        limit: int,
    ) -> list[ScoredEvidence]:
        """Hybrid ranking: reciprocal rank fusion (see reciprocal_rank_fusion)
        of the semantic rank (cosine similarity of embeddings) and the keyword
        rank (share of the query's keywords found in the evidence text and tags).

        Keywords and evidence words are compared in singular form, so a
        requirement for "REST APIs" matches a bullet about "a REST API"."""
        if len(query.vector) != filters.embedding_dimension:
            raise ValueError("query vector does not have the index's embedding dimension")
        loaded = await self._load(owner_id, profile_version, filters)
        if not loaded.documents or limit <= 0:
            return []

        similarities = cosine_similarities(np.asarray(query.vector, dtype=float), loaded.matrix)
        keywords = set(singular_tokens(" ".join(query.keywords)))
        keyword_scores = [keyword_overlap(keywords, set(tokens)) for tokens in loaded.token_sets]
        fused = reciprocal_rank_fusion([_ranking(similarities.tolist()), _ranking(keyword_scores)])
        best = sorted(fused, key=lambda index: (-fused[index], index))[:limit]
        return [
            ScoredEvidence(
                evidence=loaded.documents[index],
                similarity=float(similarities[index]),
                keyword_score=keyword_scores[index],
                fused_score=fused[index],
            )
            for index in best
        ]

    async def _load(
        self, owner_id: str, profile_version: int, filters: RetrievalFilters
    ) -> _LoadedEvidence:
        """Load and check one version's evidence (cached for this instance).

        The owner, profile and version filters are part of the MongoDB query.
        Every record must be embedded with the expected model and dimension;
        mixing vectors of different models would make similarities
        meaningless, so that is refused instead of ranked.
        """
        cache_key = (owner_id, filters.profile_id, profile_version)
        if cache_key in self._loaded:
            return self._loaded[cache_key]

        documents = await self._evidence.list_for_version(
            owner_id, filters.profile_id, profile_version, with_vectors=True
        )
        for document in documents:
            consistent = (
                document.embedding_status == "embedded"
                and document.embedding is not None
                and document.embedding_model == filters.embedding_model
                and document.embedding_dimension == filters.embedding_dimension
                and len(document.embedding) == filters.embedding_dimension
            )
            if not consistent:
                raise ProfileNotIndexed(
                    "The profile's evidence is not fully indexed with the current embedding "
                    "model. Confirm the profile again."
                )
        loaded = _LoadedEvidence(
            documents=[document.model_copy(update={"embedding": None}) for document in documents],
            matrix=np.asarray([document.embedding for document in documents], dtype=float),
            token_sets=[
                singular_tokens(document.text) | singular_tokens(" ".join(document.tags))
                for document in documents
            ],
        )
        self._loaded[cache_key] = loaded
        return loaded


# ---- Building the context for one job ----------------------------------------------


def _parent_key(evidence: EvidenceDoc) -> str:
    """The role or project an evidence record belongs to (itself if none)."""
    if evidence.parent is not None:
        return evidence.parent.record_id
    return evidence.record_id or evidence.evidence_id


def diversify_by_parent(
    ranked: list[ScoredEvidence], limit: int, max_per_parent: int = MAX_PER_PARENT
) -> list[ScoredEvidence]:
    """Pick ``limit`` candidates, preferring variety of roles and projects.

    First pass: walk the ranking and take a candidate unless its role/project
    already has ``max_per_parent`` picks. Second pass: if places are left, fill
    them with the skipped candidates in rank order. One long role therefore
    cannot crowd out a relevant project, but nothing relevant is discarded
    just to be diverse. The result keeps rank order.
    """
    picked: list[int] = []
    per_parent: dict[str, int] = {}
    for index, candidate in enumerate(ranked):
        if len(picked) == limit:
            break
        parent = _parent_key(candidate.evidence)
        if per_parent.get(parent, 0) < max_per_parent:
            per_parent[parent] = per_parent.get(parent, 0) + 1
            picked.append(index)
    skipped = [index for index in range(len(ranked)) if index not in picked]
    picked.extend(skipped[: max(0, limit - len(picked))])
    return [ranked[index] for index in sorted(picked)]


def best_statement_per_role(ranked: list[ScoredEvidence]) -> list[EvidenceDoc]:
    """The best-ranked bullet of every employment record in ``ranked``, best
    first. Only bullets count: the record of a role's header (title, dates)
    shows that the role exists but gives the model nothing to write about."""
    best: dict[str, EvidenceDoc] = {}
    for candidate in ranked:
        evidence = candidate.evidence
        if evidence.category == EMPLOYMENT_CATEGORY and evidence.bullet_id is not None:
            best.setdefault(_parent_key(evidence), evidence)
    return list(best.values())


def select_context(
    candidate_lists: list[list[ScoredEvidence]],
    max_records: int,
    token_budget: int,
    role_statements: list[EvidenceDoc] | None = None,
) -> list[EvidenceDoc]:
    """Merge the candidates of all queries into one bounded context.

    Round robin: first every query's best candidate, then every query's second
    best, and so on. Each requirement therefore gets its strongest evidence in
    before any requirement gets a second record.

    ``role_statements`` (see best_statement_per_role) come right after the
    first round. A bullet may only cite evidence of its own role, so a role
    with nothing in the context cannot get a tailored bullet. They do not
    come first because a requirement without its best evidence could no
    longer be rated as covered.

    A record is skipped when it is already selected (deduplication by
    evidence_id) or would exceed the token budget; selection stops at
    ``max_records``. The result is returned in profile order so evidence of
    the same role stays together.
    """
    deepest = max((len(candidates) for candidates in candidate_lists), default=0)
    rounds = [
        [candidates[rank].evidence for candidates in candidate_lists if rank < len(candidates)]
        for rank in range(deepest)
    ]
    ordered = [
        *(rounds[0] if rounds else []),
        *(role_statements or []),
        *(evidence for later_round in rounds[1:] for evidence in later_round),
    ]
    selected: dict[str, EvidenceDoc] = {}
    tokens_used = 0
    for evidence in ordered:
        if len(selected) == max_records:
            break
        cost = estimate_tokens(evidence.text)
        if evidence.evidence_id in selected or tokens_used + cost > token_budget:
            continue
        selected[evidence.evidence_id] = evidence
        tokens_used += cost
    return sorted(selected.values(), key=lambda evidence: evidence.position)


def role_query_text(job: JobDoc) -> str:
    """A concise description of the role as a whole: title plus the analysed
    role summary, or the start of the posting when there is no summary."""
    summary = job.role_summary or " ".join(job.description.split())
    parts = [part.strip() for part in (job.title, summary) if part and part.strip()]
    return ". ".join(parts)[:ROLE_QUERY_MAX_CHARS]


def embedded_query_text(text: str) -> str:
    """A query as it is sent for embedding: without e-mail addresses, phone
    numbers and URLs. A posting's contact details say nothing about a
    requirement, and the evidence is embedded without them as well. A text
    that consists of nothing else is kept, because an empty text cannot be
    embedded."""
    return strip_contact_details(text) or text


async def retrieve_for_job(
    retriever: Retriever,
    provider: AIProvider,
    owner_id: str,
    profile_version: int,
    job: JobDoc,
    filters: RetrievalFilters,
    *,
    per_requirement: int,
    max_context: int,
    token_budget: int,
) -> RetrievalResult:
    """Retrieve the evidence context for generating documents for ``job``.

    Makes exactly one provider call: all query texts (one per requirement,
    then the role query) are embedded together. The role query ranks the
    whole profile, which also tells which statement of each role fits the
    job best (see select_context).
    """
    texts = [requirement.text for requirement in job.requirements] + [role_query_text(job)]
    keyword_sets = [
        frozenset(tokenize(requirement.text)) | frozenset(tokenize(" ".join(requirement.keywords)))
        for requirement in job.requirements
    ] + [frozenset(tokenize(texts[-1]))]
    vectors, usage = await provider.embed([embedded_query_text(text) for text in texts])

    queries = [
        RetrievalQuery(text=text, vector=vector, keywords=keywords)
        for text, vector, keywords in zip(texts, vectors, keyword_sets, strict=True)
    ]
    pool = per_requirement * CANDIDATE_POOL_FACTOR
    rankings = [
        await retriever.retrieve(owner_id, profile_version, query, filters, limit=pool)
        for query in queries[:-1]
    ]
    # The last query is the role query.
    role_ranking = await retriever.retrieve(
        owner_id, profile_version, queries[-1], filters, limit=WHOLE_PROFILE
    )
    candidate_lists = [
        diversify_by_parent(ranked[:pool], per_requirement) for ranked in [*rankings, role_ranking]
    ]

    context = select_context(
        candidate_lists, max_context, token_budget, best_statement_per_role(role_ranking)
    )
    selected_ids = {evidence.evidence_id for evidence in context}
    candidates_by_requirement = {
        requirement.requirement_id: [
            candidate.evidence.evidence_id
            for candidate in candidates
            if candidate.evidence.evidence_id in selected_ids
        ]
        # The last candidate list belongs to the role query, not to a requirement.
        for requirement, candidates in zip(job.requirements, candidate_lists[:-1], strict=True)
    }
    return RetrievalResult(
        profile_version=profile_version,
        evidence=context,
        candidates_by_requirement=candidates_by_requirement,
        usage=usage,
    )
